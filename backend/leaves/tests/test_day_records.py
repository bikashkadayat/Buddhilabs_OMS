from datetime import timedelta
from decimal import Decimal

import pytest

from leaves.models import Leave, LeaveDayRecord, Holiday
from leaves import services
from .conftest import MONDAY


@pytest.mark.django_db
def test_five_day_leave_creates_five_records_no_weekend(maker):
    # Mon..Fri inclusive. post_save signal generates the day records.
    leave = Leave.objects.create(
        user=maker, leave_type="annual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=4),
    )
    records = leave.day_records.all()
    assert records.count() == 5
    assert all(not r.is_weekend for r in records)
    assert all(r.status == LeaveDayRecord.Status.PENDING for r in records)


@pytest.mark.django_db
def test_leave_spanning_weekend_flags_weekend_days(maker):
    # Fri..Mon inclusive = 4 calendar days, 1 of them weekend (Saturday only in Nepal).
    leave = Leave.objects.create(
        user=maker, leave_type="annual", reason="x",
        start_date=MONDAY + timedelta(days=4), end_date=MONDAY + timedelta(days=7),
    )
    records = list(leave.day_records.all())
    assert len(records) == 4
    assert sum(1 for r in records if r.is_weekend) == 1


@pytest.mark.django_db
def test_holiday_day_is_flagged_and_not_consumed(maker, annual):
    Holiday.objects.create(date=MONDAY + timedelta(days=2), name="Mid-week Holiday")
    leave = Leave.objects.create(
        user=maker, leave_type="annual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=4), status=Leave.Status.APPROVED,
    )
    holiday_record = leave.day_records.get(date=MONDAY + timedelta(days=2))
    assert holiday_record.is_holiday is True

    balance = services.recompute_leave_balance(maker, annual, 2026)
    # 5 weekdays minus the holiday => 4 consumed.
    assert balance.used_days == Decimal("4")


@pytest.mark.django_db
def test_half_day_leave_creates_half_portion_records(maker):
    # Two weekdays, both taken as half-days => two 0.5 records (total 1.0 day).
    leave = Leave.objects.create(
        user=maker, leave_type="casual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=1),
    )
    services.generate_leave_day_records(leave, day_portion=LeaveDayRecord.DayPortion.FIRST_HALF)
    records = list(leave.day_records.all())
    assert len(records) == 2
    assert all(r.portion_days == Decimal("0.5") for r in records)
    assert sum((r.portion_days for r in records), Decimal("0")) == Decimal("1.0")


@pytest.mark.django_db
def test_leave_day_portion_field_drives_half_day_generation(maker):
    # Storing FIRST_HALF on the Leave itself must make the create signal generate
    # a 0.5-weight record — no explicit generate_leave_day_records() call needed.
    leave = Leave.objects.create(
        user=maker, leave_type="casual", reason="x",
        start_date=MONDAY, end_date=MONDAY,
        day_portion=Leave.DayPortion.FIRST_HALF,
    )
    records = list(leave.day_records.all())
    assert len(records) == 1
    assert records[0].day_portion == LeaveDayRecord.DayPortion.FIRST_HALF
    assert records[0].portion_days == Decimal("0.5")


@pytest.mark.django_db
def test_serializer_reports_half_total_and_rejects_multi_day_half(maker):
    from leaves.serializers import LeaveSerializer

    single = Leave.objects.create(
        user=maker, leave_type="casual", reason="x",
        start_date=MONDAY, end_date=MONDAY,
        day_portion=Leave.DayPortion.SECOND_HALF,
    )
    assert LeaveSerializer(single).data["total_days"] == 0.5

    # A half day spanning a range is nonsensical and must not validate.
    ser = LeaveSerializer(data={
        "leave_type": "casual", "reason": "x",
        "start_date": MONDAY.isoformat(),
        "end_date": (MONDAY + timedelta(days=2)).isoformat(),
        "day_portion": Leave.DayPortion.FIRST_HALF,
    })
    assert not ser.is_valid()
    assert "same date" in str(ser.errors).lower()
