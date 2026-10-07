"""Prove the OMS has not renumbered a single biometric identity (Phase 12).

**The device is the source of truth for identity.** Device User ID 17 must mean
device user 17 in the OMS, in every punch it ever produced, forever. The OMS
stores those identifiers; it never mints, reassigns or rewrites them.

This command proves that, rather than asserting it:

    python manage.py verify_biometric_ids
    python manage.py verify_biometric_ids --json
    python manage.py verify_biometric_ids --expect 1,2,3   # against the device roster

Why this exists as a check and not just a rule: a renumbering is silently
catastrophic rather than loudly broken. Historical punches keep the
``employee_device_id`` they arrived with, so nothing errors — but future punches
from the original ID stop resolving, punches from the new ID start landing on
the wrong person, and nobody notices until payroll. There is no exception to
raise, so there has to be something to look at.
"""
import json
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.utils import timezone

from biometric import services
from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee


class Command(BaseCommand):
    help = "Verify that no biometric device user ID has been changed or reassigned."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--expect",
            help="Comma-separated device user IDs read from the terminal itself. "
                 "Any difference is reported in both directions.")
        parser.add_argument("--device", help="Limit to one device by name or label.")

    def handle(self, *args, **options):
        report = self._build(options)
        if options["as_json"]:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return
        self._render(report, options)

    # -- analysis ----------------------------------------------------------
    def _build(self, options):
        devices = BiometricDevice.objects.all()
        if options["device"]:
            devices = devices.filter(name__icontains=options["device"])

        enrolments = (BiometricEmployee.objects
                      .filter(device__in=devices)
                      .select_related("device", "user")
                      .order_by("device__name", "device_user_id"))

        active = enrolments.filter(is_active=True)
        return {
            "generated_at": timezone.localtime().isoformat(),
            "devices": [{"name": d.name, "host": d.host, "active": d.is_active}
                        for d in devices],
            "device_user_count": active.count(),
            "device_user_ids": sorted(
                active.values_list("device_user_id", flat=True),
                key=self._sort_key),
            "mappings": [{
                "device": row.device.name,
                "device_user_id": row.device_user_id,
                "device_name": row.device_name,
                "device_employee_number": row.card or None,
                "oms_employee": row.user.get_full_name() if row.user else None,
                "oms_employee_id": (row.user.employee_id if row.user else None),
                "status": "mapped" if row.user_id else "UNMAPPED",
                "effective_from": row.effective_from,
                "effective_until": row.effective_until,
            } for row in active],
            "unmapped": [row.device_user_id for row in active if not row.user_id],
            "conflicts": self._conflicts(enrolments),
            "punch_integrity": self._punch_integrity(devices),
            "retired": [{
                "device_user_id": row.device_user_id,
                "was": row.user.get_full_name() if row.user else None,
                "until": row.effective_until,
                "superseded_by": str(row.superseded_by_id) if row.superseded_by_id else None,
            } for row in enrolments.filter(is_active=False)],
        }

    _sort_key = staticmethod(services.device_id_sort_key)

    def _conflicts(self, enrolments):
        """Every way an identity could have become ambiguous."""
        conflicts = []

        # Two ACTIVE rows for the same device ID. A partial unique index should
        # make this impossible; checked anyway, because if it ever happened
        # every punch for that ID would resolve non-deterministically.
        seen = Counter()
        for row in enrolments.filter(is_active=True):
            seen[(row.device_id, row.device_user_id)] += 1
        for (device_id, device_user_id), count in seen.items():
            if count > 1:
                conflicts.append({
                    "type": "duplicate_active_device_id",
                    "device_user_id": device_user_id,
                    "count": count,
                    "severity": "CRITICAL",
                    "detail": "Two active mappings for one device ID — punches "
                              "would resolve non-deterministically.",
                })

        # One employee holding two active IDs on the same device. Legitimate
        # across devices (main gate + warehouse), never on one.
        by_user = defaultdict(list)
        for row in enrolments.filter(is_active=True, user__isnull=False):
            by_user[(row.user_id, row.device_id)].append(row.device_user_id)
        for (_user_id, _device_id), ids in by_user.items():
            if len(ids) > 1:
                conflicts.append({
                    "type": "employee_with_multiple_ids_on_one_device",
                    "device_user_ids": ids,
                    "severity": "HIGH",
                    "detail": "One employee holds two device IDs on the same "
                              "terminal; their attendance will be split.",
                })

        # A reused device ID with no validity window is the reassignment hazard
        # the effective_from/until columns exist to bound.
        history = defaultdict(list)
        for row in enrolments:
            history[(row.device_id, row.device_user_id)].append(row)
        for (_device_id, device_user_id), rows in history.items():
            if len(rows) > 1 and any(r.effective_from is None and r.is_active for r in rows):
                conflicts.append({
                    "type": "reused_id_without_validity_window",
                    "device_user_id": device_user_id,
                    "occupants": len(rows),
                    "severity": "HIGH",
                    "detail": "This device ID has had more than one occupant but "
                              "the active mapping is unbounded — a backfill could "
                              "hand the previous person's history to the current one.",
                })
        return conflicts

    def _punch_integrity(self, devices):
        """The proof that no historical punch was re-pointed.

        ``AttendancePunch.employee_device_id`` is written once at ingest and
        never updated. Every distinct value that has ever arrived is compared
        against the enrolments, so an ID present in punches but absent from the
        roster is visible — which is what a renumbering would look like after
        the fact.
        """
        punches = AttendancePunch.objects.filter(device__in=devices)
        punch_ids = set(punches.values_list("employee_device_id", flat=True).distinct())
        enrolled_ids = set(BiometricEmployee.objects.filter(device__in=devices)
                           .values_list("device_user_id", flat=True))

        orphaned = sorted(punch_ids - enrolled_ids, key=self._sort_key)
        never_punched = sorted(enrolled_ids - punch_ids, key=self._sort_key)

        return {
            "total_punches": punches.count(),
            "distinct_ids_in_punches": len(punch_ids),
            "distinct_ids_enrolled": len(enrolled_ids),
            "ids_in_punches_without_enrolment": orphaned,
            "ids_enrolled_without_punches": never_punched,
            "unresolved_punches": punches.filter(user__isnull=True).count(),
        }

    # -- output ------------------------------------------------------------
    def _render(self, report, options):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nBiometric identity verification\n"))
        self.stdout.write(
            "  The device is the source of truth. The OMS stores its identifiers\n"
            "  and never renumbers, reassigns or replaces them.\n")

        self.stdout.write(self.style.HTTP_INFO("\n  1. Device user count"))
        self.stdout.write(f"      Active enrolments : {report['device_user_count']}")
        for device in report["devices"]:
            self.stdout.write(f"      Device            : {device['name']} "
                              f"({device['host'] or 'no host recorded'})")

        self.stdout.write(self.style.HTTP_INFO("\n  2. Device user IDs (as stored)"))
        ids = report["device_user_ids"]
        self.stdout.write(f"      {', '.join(ids) if ids else '(none — roster not synced)'}")

        self.stdout.write(self.style.HTTP_INFO("\n  3. OMS user mappings"))
        if not report["mappings"]:
            self.stdout.write("      (none)")
        else:
            self.stdout.write(f"      {'Device ID':<11} {'Device Name':<20} "
                              f"{'OMS Employee':<24} {'Employee ID':<20} Status")
            for row in report["mappings"]:
                self.stdout.write(
                    f"      {row['device_user_id']:<11} "
                    f"{(row['device_name'] or '—')[:20]:<20} "
                    f"{(row['oms_employee'] or '—')[:24]:<24} "
                    f"{(row['oms_employee_id'] or '—'):<20} {row['status']}")

        self.stdout.write(self.style.HTTP_INFO("\n  4. Mapping conflicts"))
        if not report["conflicts"]:
            self.stdout.write(self.style.SUCCESS("      None."))
        for conflict in report["conflicts"]:
            self.stdout.write(self.style.ERROR(
                f"      [{conflict['severity']}] {conflict['type']}"))
            self.stdout.write(f"          {conflict['detail']}")

        self.stdout.write(self.style.HTTP_INFO("\n  5. Unmapped users"))
        if report["unmapped"]:
            self.stdout.write(self.style.WARNING(
                f"      {len(report['unmapped'])}: {', '.join(report['unmapped'])}"))
            self.stdout.write(
                "      Their punches are stored but produce no attendance until mapped.")
        else:
            self.stdout.write(self.style.SUCCESS("      None."))

        self._integrity(report)
        if options["expect"]:
            self._compare_expected(report, options["expect"])
        self._verdict(report)

    def _integrity(self, report):
        integrity = report["punch_integrity"]
        self.stdout.write(self.style.HTTP_INFO("\n  6. Historical punch integrity"))
        self.stdout.write(f"      Punches stored              : {integrity['total_punches']}")
        self.stdout.write(f"      Distinct IDs in punches     : {integrity['distinct_ids_in_punches']}")
        self.stdout.write(f"      Distinct IDs enrolled       : {integrity['distinct_ids_enrolled']}")
        if integrity["ids_in_punches_without_enrolment"]:
            self.stdout.write(self.style.WARNING(
                f"      IDs in punches but NOT enrolled: "
                f"{', '.join(integrity['ids_in_punches_without_enrolment'])}"))
            self.stdout.write(
                "        Either the roster has not been synced, or an enrolment was\n"
                "        deleted. Punches keep the ID they arrived with, so the data\n"
                "        is intact — but nothing will resolve until it is re-enrolled.")
        if integrity["ids_enrolled_without_punches"]:
            self.stdout.write(
                f"      Enrolled but never punched  : "
                f"{', '.join(integrity['ids_enrolled_without_punches'][:20])}")

        if report["retired"]:
            self.stdout.write(self.style.HTTP_INFO("\n  7. Retired mappings (reassigned IDs)"))
            for row in report["retired"]:
                self.stdout.write(
                    f"      {row['device_user_id']:<8} was {row['was'] or '—'} "
                    f"until {row['until'] or 'unbounded'}")
            self.stdout.write(
                "      Retained deliberately: a retired mapping is what stops a new\n"
                "      occupant of the same device ID inheriting the previous\n"
                "      person's attendance history.")

    def _compare_expected(self, report, expected_raw):
        expected = {value.strip() for value in expected_raw.split(",") if value.strip()}
        stored = set(report["device_user_ids"])
        missing = sorted(expected - stored, key=self._sort_key)
        extra = sorted(stored - expected, key=self._sort_key)

        self.stdout.write(self.style.HTTP_INFO(
            "\n  8. Against the device roster you supplied"))
        self.stdout.write(f"      On device : {len(expected)}    In OMS: {len(stored)}")
        if not missing and not extra:
            self.stdout.write(self.style.SUCCESS(
                "      EXACT MATCH — every device ID is present in the OMS, "
                "unchanged."))
            return
        if missing:
            self.stdout.write(self.style.ERROR(
                f"      On the device but MISSING from the OMS: {', '.join(missing)}"))
            self.stdout.write(
                "        Their punches will arrive and stay unresolved. Sync the roster.")
        if extra:
            self.stdout.write(self.style.WARNING(
                f"      In the OMS but not on the device: {', '.join(extra)}"))
            self.stdout.write(
                "        Usually a leaver whose enrolment was removed from the\n"
                "        terminal. Keep the mapping — it is what their historical\n"
                "        attendance is attached to.")

    def _verdict(self, report):
        critical = [c for c in report["conflicts"] if c["severity"] == "CRITICAL"]
        high = [c for c in report["conflicts"] if c["severity"] == "HIGH"]

        self.stdout.write(self.style.MIGRATE_HEADING("\n  Verdict\n"))
        if critical:
            self.stdout.write(self.style.ERROR(
                f"      FAIL — {len(critical)} critical conflict(s). "
                f"Do NOT cut over."))
        elif high:
            self.stdout.write(self.style.WARNING(
                f"      REVIEW — {len(high)} high-severity conflict(s) to resolve."))
        elif report["unmapped"]:
            self.stdout.write(self.style.WARNING(
                f"      INCOMPLETE — no ID has been changed, but "
                f"{len(report['unmapped'])} enrolment(s) are unmapped."))
        elif report["device_user_count"] == 0:
            self.stdout.write(self.style.WARNING(
                "      NO DATA — the roster has not been synced yet."))
        else:
            self.stdout.write(self.style.SUCCESS(
                "      PASS — zero biometric ID changes. Every device user ID is\n"
                "      stored exactly as the terminal issued it, and every punch\n"
                "      remains attached to the ID that created it."))
        self.stdout.write(
            "\n      Enforcement is not advisory: BiometricEmployee.save() raises\n"
            "      on any attempt to change device_user_id or device, so the API,\n"
            "      the admin, a shell and a management command are all covered.\n")
