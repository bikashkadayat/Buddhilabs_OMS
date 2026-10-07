from datetime import timedelta
from decimal import Decimal

import pytest

from leaves.models import Leave, EnterpriseLeaveBalance
from leaves import services
from .conftest import MONDAY


@pytest.mark.django_db
def test_balance_recompute_matches_approved_records(maker, annual):
    leave = Leave.objects.create(
        user=maker, leave_type="annual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=4),  # 5 weekdays
    )
    leave.status = Leave.Status.APPROVED
    leave.save()  # signal syncs records -> approved and recomputes

    balance = EnterpriseLeaveBalance.objects.get(user=maker, leave_type=annual, year=2026)
    assert balance.used_days == Decimal("5")
    assert balance.pending_days == Decimal("0")
    assert balance.entitled_days == Decimal("18.00")  # ANNUAL default
    assert balance.available_days == Decimal("13")  # 18 + 0 - 5 - 0


@pytest.mark.django_db
def test_pending_leave_counts_as_pending(maker, annual):
    Leave.objects.create(
        user=maker, leave_type="annual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=2),  # 3 weekdays, pending
    )
    balance = EnterpriseLeaveBalance.objects.get(user=maker, leave_type=annual, year=2026)
    assert balance.pending_days == Decimal("3")
    assert balance.used_days == Decimal("0")


@pytest.mark.django_db
def test_year_end_carry_forward_caps_at_max(maker, annual):
    leave = Leave.objects.create(
        user=maker, leave_type="annual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=4),  # 5 used
    )
    leave.status = Leave.Status.APPROVED
    leave.save()

    services.process_year_end_carry_forward(maker, 2026, actor=maker)

    this_year = EnterpriseLeaveBalance.objects.get(user=maker, leave_type=annual, year=2026)
    next_year = EnterpriseLeaveBalance.objects.get(user=maker, leave_type=annual, year=2027)
    # unused = 18 - 5 = 13; cap 9 => carry 9, forfeit 4
    assert next_year.carried_forward_days == Decimal("9.00")
    assert this_year.forfeited_days == Decimal("4")
