from datetime import date, timedelta
from decimal import Decimal

import pytest

from leaves.models import Leave, LeavePolicy
from leaves import services
from .conftest import MONDAY


@pytest.mark.django_db
def test_weekly_summary_aggregation(maker):
    # Mon..Tue SICK, approved => 2 approved days in ISO week 24.
    leave = Leave.objects.create(
        user=maker, leave_type="sick", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=1),
        status=Leave.Status.APPROVED,
    )
    summary = services.recompute_weekly_summary(maker, 2026, 24)
    assert summary.approved_days == Decimal("2")
    # 6 working days: Mon..Sun with only Saturday excluded (Nepal weekend).
    assert summary.working_days == 6
    assert summary.by_type.get("SICK") == "2.0"
    # attendance = (6 - 2) / 6 * 100
    assert summary.attendance_percentage == Decimal("66.67")
    assert leave.day_records.count() == 2


@pytest.mark.django_db
def test_monthly_summary_aggregation(maker):
    Leave.objects.create(
        user=maker, leave_type="casual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=2),  # 3 weekdays, pending
    )
    summary = services.recompute_monthly_summary(maker, 2026, MONDAY.month)
    assert summary.pending_days == Decimal("3")
    assert summary.by_type.get("CASUAL") == "3.0"


@pytest.mark.django_db
def test_policy_resolution_department_overrides_org(maker, annual, eng_department):
    maker.department_ref = eng_department
    maker.save(update_fields=["department_ref"])

    LeavePolicy.objects.create(
        leave_type=annual, department=None, role=None,
        days_per_year=Decimal("18.00"), effective_from=date(2026, 1, 1),
    )
    LeavePolicy.objects.create(
        leave_type=annual, department=eng_department, role=None,
        days_per_year=Decimal("25.00"), effective_from=date(2026, 1, 1),
    )

    policy = services.resolve_leave_policy(maker, annual, date(2026, 6, 1))
    assert policy.days_per_year == Decimal("25.00")

    balance = services.recompute_leave_balance(maker, annual, 2026)
    assert balance.entitled_days == Decimal("25.00")


@pytest.mark.django_db
def test_policy_resolution_falls_back_to_default(maker, annual):
    # No policy configured => LeaveType default (18) is used.
    policy = services.resolve_leave_policy(maker, annual, date(2026, 6, 1))
    assert policy is None
    balance = services.recompute_leave_balance(maker, annual, 2026)
    assert balance.entitled_days == Decimal("18.00")
