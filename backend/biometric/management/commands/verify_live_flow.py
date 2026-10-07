"""End-to-end verification of the live attendance flow (Phase 12).

Proves the whole chain works on THIS deployment, with real signing, the real
ingest endpoint, the real derivation engine and the real dashboard aggregate:

    signed punch -> OMS API -> AttendancePunch -> derivation -> Attendance -> dashboard

    python manage.py verify_live_flow --device "Main Gate" --api-key <raw key>
    python manage.py verify_live_flow --device "Main Gate" --api-key <key> --keep

Everything it creates is removed again unless ``--keep`` is passed, and it
refuses to run against a real employee: it uses a dedicated throwaway user and a
dedicated enrolment, so a verification run can never contaminate somebody's
actual attendance record.

Why a synthetic punch rather than asking someone to touch the sensor: the human
test proves the sensor works, this proves everything AFTER the sensor works, and
you want to know which half is broken before you go looking. Do both — the
manual one is UAT case BIO-03.
"""
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import Client
from django.utils import timezone

from attendance.models import Attendance
from biometric import authentication
from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee
from users.models import User

PROBE_USERNAME = "_flow_probe"
PROBE_DEVICE_USER_ID = "999999"


class Command(BaseCommand):
    help = "Verify the punch -> attendance -> dashboard chain end to end."

    def add_arguments(self, parser):
        parser.add_argument("--device", required=True,
                            help="BiometricDevice name or label.")
        parser.add_argument("--api-key", required=True,
                            help="The device's RAW api key (shown once at issue).")
        parser.add_argument("--keep", action="store_true",
                            help="Leave the probe data in place for inspection.")

    def handle(self, *args, **options):
        device = self._device(options["device"])
        steps = []
        created = {}

        try:
            with transaction.atomic():
                created["user"], created["enrolment"] = self._fixtures(device)
            steps.append(self._ok("Probe employee + enrolment created",
                                  f"user={created['user'].username}, "
                                  f"device_user_id={PROBE_DEVICE_USER_ID}"))

            punch_payload, response = self._send_punch(device, options["api_key"])
            steps.append(self._step(
                "Signed punch accepted by the ingest API",
                response.status_code in (200, 201),
                f"HTTP {response.status_code} {getattr(response, 'data', '')}"))
            if response.status_code not in (200, 201):
                raise CommandError("Ingest rejected the punch — chain broken at the API.")

            punch = AttendancePunch.objects.filter(
                device=device, employee_device_id=PROBE_DEVICE_USER_ID).first()
            steps.append(self._step("AttendancePunch stored", punch is not None,
                                    f"punch id={punch.pk if punch else '—'}"))

            steps.append(self._step(
                "Punch resolved to the mapped employee",
                punch is not None and punch.user_id == created["user"].pk,
                f"user={punch.user if punch and punch.user else 'UNMAPPED'}"))

            self._derive()
            attendance = Attendance.objects.filter(
                employee=created["user"], date=punch.local_date).first() if punch else None
            steps.append(self._step(
                "Attendance row derived", attendance is not None,
                f"status={attendance.status if attendance else '—'}, "
                f"source={attendance.source if attendance else '—'}"))

            steps.append(self._step(
                "Derived from the device, not the browser",
                attendance is not None and attendance.source == Attendance.Source.BIOMETRIC,
                f"source={attendance.source if attendance else '—'}"))

            steps.append(self._dashboard(created["user"]))

        finally:
            if options["keep"]:
                self.stdout.write(self.style.WARNING(
                    "\n--keep: probe user, enrolment, punch and attendance were "
                    "LEFT IN PLACE. Remove them before go-live."))
            else:
                self._cleanup(created)
                steps.append(self._ok("Probe data removed", "nothing left behind"))

        self._render(steps)

    # -- pieces ------------------------------------------------------------
    def _device(self, name):
        device = (BiometricDevice.objects.filter(name__iexact=name).first()
                  or BiometricDevice.objects.filter(label__iexact=name).first())
        if device is None:
            raise CommandError(
                f"No device named {name!r}. Registered: "
                f"{', '.join(BiometricDevice.objects.values_list('name', flat=True)) or 'none'}")
        if not device.is_active:
            raise CommandError(f"Device {device.name} is inactive.")
        return device

    def _fixtures(self, device):
        """A throwaway employee and enrolment.

        Deliberately NOT a real person: deriving attendance for a real employee
        from a fake punch would put a fabricated day on their record, and the
        whole point of this system is that attendance records are trustworthy.
        """
        user, _ = User.objects.get_or_create(
            username=PROBE_USERNAME,
            defaults={
                "email": "flow.probe@nif.invalid",
                "first_name": "Flow", "last_name": "Probe",
                "role": User.Roles.MAKER, "is_active": True,
                "date_of_joining": timezone.localdate() - timedelta(days=365),
            })
        enrolment, _ = BiometricEmployee.objects.get_or_create(
            device=device, device_user_id=PROBE_DEVICE_USER_ID,
            defaults={"user": user, "device_name": "Flow Probe", "is_active": True})
        if enrolment.user_id != user.pk:
            enrolment.user = user
            enrolment.save(update_fields=["user"])
        return user, enrolment

    def _send_punch(self, device, raw_key):
        """A real signed request through the real endpoint — same headers the
        collector sends, so a signing or clock-skew problem shows up here."""
        now = timezone.localtime()
        path = "/api/v1/biometric/punch/"
        payload = {
            "employee_device_id": PROBE_DEVICE_USER_ID,
            "timestamp": now.isoformat(),
            "punch": 0,
            "source": "live",
            "queue_depth": 0,
        }
        import json as _json

        body = _json.dumps(payload).encode()
        timestamp = str(int(time.time()))
        signature = authentication.sign(
            authentication.hashlib.sha256(raw_key.encode()).hexdigest(),
            "POST", path, timestamp, body)

        response = Client().post(
            path, data=body, content_type="application/json",
            HTTP_X_API_KEY=raw_key, HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_SIGNATURE=signature)
        return payload, response

    def _derive(self):
        from biometric import derivation

        derivation.process_unprocessed_punches()

    def _dashboard(self, user):
        """The read side: the aggregate the workforce dashboards actually call."""
        try:
            from attendance.workforce import aggregates

            counts, per_employee, _holiday = aggregates.day_counts(
                [user], timezone.localdate())
            status = per_employee.get(user.pk)
            return self._step(
                "Visible on the dashboard aggregate", status is not None,
                f"resolved status={status}")
        except Exception as exc:  # noqa: BLE001
            return self._step("Visible on the dashboard aggregate", False,
                              f"{type(exc).__name__}: {exc}")

    def _cleanup(self, created):
        user = created.get("user")
        if user is None:
            return
        Attendance.objects.filter(employee=user).delete()
        AttendancePunch.objects.filter(employee_device_id=PROBE_DEVICE_USER_ID).delete()
        BiometricEmployee.objects.filter(
            device_user_id=PROBE_DEVICE_USER_ID).delete()
        User.objects.filter(username=PROBE_USERNAME).delete()

    # -- output ------------------------------------------------------------
    @staticmethod
    def _step(name, ok, detail):
        return {"step": name, "ok": ok, "detail": detail}

    @staticmethod
    def _ok(name, detail):
        return {"step": name, "ok": True, "detail": detail}

    def _render(self, steps):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nLive attendance flow\n"))
        for index, step in enumerate(steps, start=1):
            mark = self.style.SUCCESS("PASS") if step["ok"] else self.style.ERROR("FAIL")
            self.stdout.write(f"  {index}. [{mark}] {step['step']}")
            self.stdout.write(f"           {step['detail']}")

        failed = [s for s in steps if not s["ok"]]
        self.stdout.write("")
        if failed:
            self.stdout.write(self.style.ERROR(
                f"  CHAIN BROKEN at: {failed[0]['step']}"))
        else:
            self.stdout.write(self.style.SUCCESS(
                "  Full chain verified: punch -> API -> AttendancePunch -> "
                "derivation -> Attendance -> dashboard.\n"
                "  This proves everything AFTER the sensor. Also run UAT case "
                "BIO-03 with a real finger."))
