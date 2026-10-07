from datetime import timedelta

import pytest

from audit.models import AuditLog
from leaves.models import Leave
from leaves import services
from .conftest import MONDAY


@pytest.mark.django_db
def test_audit_log_immutability(maker):
    entry = services.audit_log(maker, "LEAVE_POLICY_APPLIED", metadata={"x": 1})
    assert isinstance(entry, AuditLog)

    entry.changes = {"tampered": True}
    with pytest.raises(ValueError):
        entry.save()
    with pytest.raises(ValueError):
        entry.delete()


@pytest.mark.django_db
def test_my_history_endpoint(api, maker):
    Leave.objects.create(
        user=maker, leave_type="annual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=2),
    )
    api.force_authenticate(maker)
    resp = api.get("/api/v1/leaves/my-history/?year=2026")
    assert resp.status_code == 200
    assert set(["user", "year", "balances", "recent_leaves", "monthly_summaries"]).issubset(resp.data.keys())
    assert resp.data["year"] == 2026
    assert len(resp.data["recent_leaves"]) == 1


@pytest.mark.django_db
def test_calendar_endpoint_returns_day_records(api, maker):
    Leave.objects.create(
        user=maker, leave_type="annual", reason="x",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=4),
    )
    api.force_authenticate(maker)
    resp = api.get(f"/api/v1/leaves/calendar/?start={MONDAY}&end={MONDAY + timedelta(days=4)}")
    assert resp.status_code == 200
    assert len(resp.data) == 5
    assert "display_color" in resp.data[0]


@pytest.mark.django_db
def test_recompute_balance_admin_only(api, maker, admin):
    api.force_authenticate(maker)
    assert api.post("/api/v1/leaves/recompute-balance/", {"user_id": str(maker.id), "year": 2026}).status_code == 403

    api.force_authenticate(admin)
    resp = api.post("/api/v1/leaves/recompute-balance/", {"user_id": str(maker.id), "year": 2026})
    assert resp.status_code == 200


@pytest.mark.django_db
def test_year_end_carry_forward_admin_only(api, maker, admin):
    api.force_authenticate(maker)
    assert api.post("/api/v1/leaves/year-end-carry-forward/", {"year": 2026}).status_code == 403

    api.force_authenticate(admin)
    resp = api.post("/api/v1/leaves/year-end-carry-forward/", {"year": 2026})
    assert resp.status_code == 200
    assert resp.data["year"] == 2026


@pytest.mark.django_db
def test_team_attendance_requires_privileged_role(api, maker, checker):
    api.force_authenticate(maker)
    assert api.get("/api/v1/leaves/team-attendance/?month=2026-06").status_code == 403

    api.force_authenticate(checker)
    resp = api.get("/api/v1/leaves/team-attendance/?department=ENG&month=2026-06")
    assert resp.status_code == 200
    assert "team" in resp.data


@pytest.mark.django_db
def test_leave_types_endpoint_lists_seeds(api, maker):
    api.force_authenticate(maker)
    resp = api.get("/api/v1/leave-types/")
    assert resp.status_code == 200
    codes = {t["code"] for t in resp.data["results"]}
    assert {"SICK", "CASUAL", "ANNUAL", "MATERNITY", "UNPAID"}.issubset(codes)
