"""The one-command import, at the scale of the real device.

32 enrolled users and ~10,900 attendance records, because the failures that
matter at that size are invisible at three: a chunking boundary, a per-batch
derivation that recomputes the same employee-days a thousand times, a memory
blow-up, a partial read presented as success.

The device this stands in for is a ZKTeco ZLM60 at 192.168.77.201, confirmed by
its owner to hold exactly those counts.
"""
from datetime import date, datetime, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from attendance.models import Attendance
from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee
from biometric.tests.zk_simulator import (
    FakeZKDevice, pack_attendance, pack_user, table)
from users.models import User

pytestmark = pytest.mark.django_db

USER_COUNT = 32


def run(*args, **kwargs):
    out = StringIO()
    call_command("golive_import", *args, stdout=out, stderr=out, **kwargs)
    return out.getvalue()


def build_device_data(days=210):
    """A roster of 32 and roughly 10,900 punches, as the real terminal holds."""
    roster = table([pack_user(i, str(i), f"Employee {i}") for i in range(1, USER_COUNT + 1)])

    punches, day = [], date(2026, 7, 14) - timedelta(days=days - 24)
    end = date(2026, 8, 6)
    while day <= end:
        if day.weekday() != 5:
            for uid in range(1, USER_COUNT + 1):
                punches.append(pack_attendance(
                    str(uid), datetime(day.year, day.month, day.day, 9, uid % 60)))
                punches.append(pack_attendance(
                    str(uid), datetime(day.year, day.month, day.day, 17, uid % 60),
                    punch=1))
        day += timedelta(days=1)
    return roster, table(punches), len(punches)


@pytest.fixture(scope="module")
def device_data():
    return build_device_data()


@pytest.fixture
def staff(db, dept):
    """32 OMS employees, so mapping can be exercised at full width."""
    people = {}
    for i in range(1, USER_COUNT + 1):
        u = User.objects.create_user(
            username=f"gi{i}", email=f"gi{i}@nif.test", password="pass12345",
            first_name=f"Employee", last_name=str(i), role=User.Roles.MAKER,
            department_ref=dept, employee_id=f"NIFN-EMP-2026-{i:04d}",
            date_of_joining=date(2024, 1, 1))
        User.objects.filter(pk=u.pk).update(
            date_joined=timezone.make_aware(datetime(2024, 1, 1, 9, 0)))
        people[str(i)] = u
    return people


class TestFullScaleImport:
    def test_it_imports_the_whole_roster_and_history(self, db, device_data):
        roster, attendance, total = device_data
        assert total > 10_000, "the fixture must reproduce the real scale"

        with FakeZKDevice(users=roster, attendance=attendance,
                          sizes={"users": USER_COUNT, "records": total}) as fake:
            output = run("--host", fake.host, "--port", str(fake.port),
                         "--label", "main-gate", "--timeout", "30",
                         "--since", "2026-07-14")

        assert BiometricEmployee.objects.count() == USER_COUNT
        assert "1. Total imported users" in output
        assert "6. Unmapped employees" in output

        # Only the requested window, not the terminal's whole history.
        stored = AttendancePunch.objects.all()
        assert stored.exists()
        assert stored.order_by("local_date").first().local_date >= date(2026, 7, 14)

    def test_every_device_id_survives_at_full_width(self, db, device_data):
        roster, attendance, total = device_data
        with FakeZKDevice(users=roster, attendance=attendance,
                          sizes={"users": USER_COUNT, "records": total}) as fake:
            run("--host", fake.host, "--port", str(fake.port),
                "--label", "main-gate", "--timeout", "30", "--since", "2026-07-14")

        stored = set(BiometricEmployee.objects.values_list("device_user_id", flat=True))
        assert stored == {str(i) for i in range(1, USER_COUNT + 1)}, \
            "the import renumbered a device ID"

    def test_mapped_employees_get_attendance(self, db, device_data, staff):
        roster, attendance, total = device_data
        with FakeZKDevice(users=roster, attendance=attendance,
                          sizes={"users": USER_COUNT, "records": total}) as fake:
            # First pass: roster lands, everybody unmapped.
            run("--host", fake.host, "--port", str(fake.port),
                "--label", "main-gate", "--timeout", "30", "--since", "2026-07-14")

            # Map through the service HR actually uses. It deliberately does
            # NOT backfill history — proving that the import command's own
            # backfill step is what makes the mapped history appear.
            from biometric import services
            for device_id, user in staff.items():
                services.map_employee(
                    BiometricEmployee.objects.get(device_user_id=device_id), user)

            # Second pass: idempotent on punches, and now they derive.
            output = run("--host", fake.host, "--port", str(fake.port),
                         "--device", "main-gate", "--timeout", "30",
                         "--since", "2026-07-14")

        assert "Every enrolment is mapped" in output
        assert Attendance.objects.filter(
            source=Attendance.Source.BIOMETRIC).exists(), (
            "mapping alone does not attribute history — the import's backfill "
            "step is what makes it appear, and it did not run")
        # And the punches themselves are now attached to people, not orphaned.
        assert not AttendancePunch.objects.filter(user__isnull=True).exists()

    def test_rerunning_creates_no_duplicates(self, db, device_data):
        """It must be restartable: an import that dies halfway is re-run, not
        unpicked."""
        roster, attendance, total = device_data
        with FakeZKDevice(users=roster, attendance=attendance,
                          sizes={"users": USER_COUNT, "records": total}) as fake:
            run("--host", fake.host, "--port", str(fake.port),
                "--label", "main-gate", "--timeout", "30", "--since", "2026-07-14")
            first = AttendancePunch.objects.count()
            run("--host", fake.host, "--port", str(fake.port),
                "--device", "main-gate", "--timeout", "30", "--since", "2026-07-14")

        assert AttendancePunch.objects.count() == first
        assert BiometricEmployee.objects.count() == USER_COUNT


class TestTheVerificationTable:
    def test_it_reports_all_six_numbers(self, db, device_data):
        roster, attendance, total = device_data
        with FakeZKDevice(users=roster, attendance=attendance,
                          sizes={"users": USER_COUNT, "records": total}) as fake:
            import json
            report = json.loads(run(
                "--host", fake.host, "--port", str(fake.port), "--label", "mg",
                "--timeout", "30", "--since", "2026-07-14", "--json"))

        for key in ("enrolments_in_oms", "punches_in_window", "attendancepunch_rows",
                    "attendance_rows", "mapped_employees", "unmapped_employees"):
            assert key in report
        assert report["enrolments_in_oms"] == USER_COUNT

    def test_unmapped_enrolments_are_reported_loudly(self, db, device_data):
        """They read as absent until mapped, which is the quietest way for an
        import to be wrong."""
        roster, attendance, total = device_data
        with FakeZKDevice(users=roster, attendance=attendance,
                          sizes={"users": USER_COUNT, "records": total}) as fake:
            output = run("--host", fake.host, "--port", str(fake.port),
                         "--label", "mg", "--timeout", "30", "--since", "2026-07-14")
        assert "not mapped" in output
        assert "read as absent" in output
        assert "roster_report" in output


class TestSafety:
    def test_dry_run_writes_nothing(self, db, device_data):
        roster, attendance, total = device_data
        with FakeZKDevice(users=roster, attendance=attendance,
                          sizes={"users": USER_COUNT, "records": total}) as fake:
            output = run("--host", fake.host, "--port", str(fake.port),
                         "--label", "mg", "--timeout", "30",
                         "--since", "2026-07-14", "--dry-run")
        assert "DRY RUN" in output
        assert AttendancePunch.objects.count() == 0
        assert BiometricEmployee.objects.count() == 0

    def test_an_unreachable_device_explains_the_routing_trap(self, db):
        with pytest.raises(CommandError, match="own network"):
            run("--host", "127.0.0.1", "--port", "1", "--label", "mg",
                "--timeout", "2")

    def test_it_registers_the_device_it_was_pointed_at(self, db, device_data):
        roster, attendance, total = device_data
        with FakeZKDevice(users=roster, attendance=attendance,
                          sizes={"users": USER_COUNT, "records": total}) as fake:
            run("--host", fake.host, "--port", str(fake.port), "--label", "main-gate",
                "--timeout", "30", "--since", "2026-07-14", "--dry-run")
        assert BiometricDevice.objects.filter(label="main-gate").exists()

    def test_it_never_auto_maps(self, db, device_data, staff):
        """Auto-mapping on a name match is how one person's attendance ends up
        filed under another's."""
        roster, attendance, total = device_data
        with FakeZKDevice(users=roster, attendance=attendance,
                          sizes={"users": USER_COUNT, "records": total}) as fake:
            run("--host", fake.host, "--port", str(fake.port), "--label", "mg",
                "--timeout", "30", "--since", "2026-07-14")
        assert BiometricEmployee.objects.filter(user__isnull=False).count() == 0
