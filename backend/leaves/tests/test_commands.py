from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from leaves.models import Leave, LeaveType, Holiday, EnterpriseLeaveBalance
from .conftest import MONDAY


def _approved_leave(user):
    leave = Leave.objects.create(
        user=user, leave_type="annual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=4),  # 5 weekdays
    )
    leave.status = Leave.Status.APPROVED
    leave.save()
    return leave


@pytest.mark.django_db
def test_audit_data_integrity_clean_on_valid_data(maker):
    _approved_leave(maker)
    out = StringIO()
    # Clean data => command returns normally (no SystemExit).
    call_command("audit_data_integrity", stdout=out)
    assert "CLEAN" in out.getvalue()


@pytest.mark.django_db
def test_audit_data_integrity_detects_corrupt_balance(maker, annual):
    _approved_leave(maker)
    balance = EnterpriseLeaveBalance.objects.get(user=maker, leave_type=annual, year=2026)
    balance.used_days = Decimal("999.00")  # corrupt it directly
    balance.save(update_fields=["used_days"])

    out = StringIO()
    with pytest.raises(SystemExit) as exc:
        call_command("audit_data_integrity", stdout=out)
    assert exc.value.code == 1
    assert "Balance" in out.getvalue()


@pytest.mark.django_db
def test_audit_data_integrity_fix_repairs_balance(maker, annual):
    _approved_leave(maker)
    balance = EnterpriseLeaveBalance.objects.get(user=maker, leave_type=annual, year=2026)
    balance.used_days = Decimal("999.00")
    balance.save(update_fields=["used_days"])

    out = StringIO()
    call_command("audit_data_integrity", "--fix", stdout=out)  # no SystemExit on success
    balance.refresh_from_db()
    assert balance.used_days == Decimal("5")
    assert "repaired" in out.getvalue().lower()


@pytest.mark.django_db
def test_recompute_all_balances_command(maker):
    _approved_leave(maker)
    out = StringIO()
    call_command("recompute_all_balances", "--year", "2026", "--user-id", str(maker.id), stdout=out)
    assert EnterpriseLeaveBalance.objects.filter(user=maker, year=2026).count() == LeaveType.objects.filter(is_active=True).count()


@pytest.mark.django_db
def test_recompute_summaries_command(maker):
    _approved_leave(maker)
    out = StringIO()
    call_command("recompute_summaries", "--scope", "both", "--year", "2026", stdout=out)
    assert "weekly" in out.getvalue()


@pytest.mark.django_db
def test_process_year_end_locks_and_refuses_second_run(maker):
    _approved_leave(maker)
    call_command("process_year_end", "--year", "2026", stdout=StringIO())

    assert EnterpriseLeaveBalance.objects.filter(year=2026, locked_at__isnull=False).exists()

    with pytest.raises(CommandError):
        call_command("process_year_end", "--year", "2026", stdout=StringIO())

    # --force overrides
    call_command("process_year_end", "--year", "2026", "--force", stdout=StringIO())


@pytest.mark.django_db
def test_seed_leave_types_is_idempotent():
    call_command("seed_leave_types", stdout=StringIO())
    call_command("seed_leave_types", stdout=StringIO())
    assert LeaveType.objects.filter(code="ANNUAL").count() == 1
    # 5 command-managed canonical types (incl. COMPENSATORY) + 2 engine types
    # (Maternity/Paternity) seeded by migration 0013 => 7, no duplicates.
    assert LeaveType.objects.count() == 7


@pytest.mark.django_db
def test_seed_holidays_from_fixture():
    call_command("seed_holidays", "--year", "2026", "--country", "NP", stdout=StringIO())
    assert Holiday.objects.filter(date="2026-05-01").exists()
