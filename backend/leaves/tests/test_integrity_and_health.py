from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import transaction
from django.db.models import ProtectedError

from leaves.models import Leave, LeaveDayRecord, EnterpriseLeaveBalance
from leaves import services
from .conftest import MONDAY


def _approved_leave(user):
    leave = Leave.objects.create(
        user=user, leave_type="annual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=4),
    )
    leave.status = Leave.Status.APPROVED
    leave.save()
    return leave


@pytest.mark.django_db
def test_cannot_hard_delete_leave_with_approved_records(maker):
    leave = _approved_leave(maker)
    # Wrap in a savepoint so the expected error doesn't poison the test's
    # outer transaction (it rolls back only to the savepoint).
    with pytest.raises(ProtectedError):
        with transaction.atomic():
            leave.delete()
    assert Leave.objects.filter(pk=leave.pk).exists()


@pytest.mark.django_db
def test_soft_delete_frees_balance(maker, annual):
    leave = _approved_leave(maker)
    assert EnterpriseLeaveBalance.objects.get(user=maker, leave_type=annual, year=2026).used_days == Decimal("5")

    services.soft_delete_leave(leave)

    leave.refresh_from_db()
    assert leave.is_deleted is True
    assert leave.deleted_at is not None
    assert all(r.status == LeaveDayRecord.Status.CANCELLED for r in leave.day_records.all())
    assert EnterpriseLeaveBalance.objects.get(user=maker, leave_type=annual, year=2026).used_days == Decimal("0")


@pytest.mark.django_db
def test_pending_leave_can_still_be_hard_deleted(maker):
    leave = Leave.objects.create(
        user=maker, leave_type="casual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=1),
    )
    leave.delete()  # no approved records -> allowed
    assert not Leave.objects.filter(pk=leave.pk).exists()


@pytest.mark.django_db
def test_pre_save_corrects_day_flags(maker):
    leave = _approved_leave(maker)
    record = leave.day_records.first()  # a weekday record
    record.is_weekend = True   # deliberately wrong
    record.is_holiday = True   # deliberately wrong
    record.save()
    record.refresh_from_db()
    assert record.is_weekend is False
    assert record.is_holiday is False


@pytest.mark.django_db
def test_soft_deleted_leave_hidden_from_api(api, maker):
    leave = _approved_leave(maker)
    services.soft_delete_leave(leave)
    api.force_authenticate(maker)
    resp = api.get("/api/v1/leaves/")
    ids = [row["id"] for row in resp.data["results"]]
    assert str(leave.id) not in ids


@pytest.mark.django_db
def test_health_endpoint_is_public(api):
    resp = api.get("/api/v1/health/")
    assert resp.status_code == 200
    assert resp.data["database"] == "up"


@pytest.mark.django_db
def test_detailed_health_requires_admin(api, maker, admin):
    api.force_authenticate(maker)
    assert api.get("/api/v1/health/detailed/").status_code == 403

    api.force_authenticate(admin)
    resp = api.get("/api/v1/health/detailed/")
    assert resp.status_code == 200
    assert set(["queues", "disk", "last_cron_runs"]).issubset(resp.data.keys())
