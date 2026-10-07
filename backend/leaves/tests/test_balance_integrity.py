"""
Cluster A regression tests — simple LeaveBalance is a derived projection of the
LeaveDayRecord source of truth, kept correct across the two-stage workflow.

Covers:
  H4 — pending leave reserves balance; over-application is blocked.
  M3 — an approved leave is deducted exactly once (no double-deduct, no stage skip).
  H3 — cancelling an approved leave refunds the balance.
"""
from datetime import timedelta

import pytest
from rest_framework.test import APIClient

from users.models import User
from leaves.models import Leave, LeaveBalance
from .conftest import _user, MONDAY, YEAR


FRI = MONDAY + timedelta(days=4)  # Mon..Fri inclusive = 5 working days (no Saturday)


@pytest.fixture
def approver(db):
    return _user("lapprover", User.Roles.APPROVER)


def _apply(client, approver, start, end):
    return client.post("/api/v1/leaves/", {
        "leave_type": "annual",
        "start_date": str(start),
        "end_date": str(end),
        "reason": "Test leave",
        "approver": str(approver.id),
    }, format="json")


def _balance(user):
    bal = LeaveBalance.objects.filter(user=user, leave_type="annual", year=YEAR).first()
    return bal.used_so_far if bal else 0


@pytest.mark.django_db
def test_pending_leave_reserves_balance_and_blocks_overapplication(maker, checker, approver):
    """H4: a pending application immediately consumes balance; a second request
    that would exceed the remainder (18 annual) is rejected."""
    client = APIClient(); client.force_authenticate(maker)

    resp = _apply(client, approver, MONDAY, FRI)
    assert resp.status_code == 201, resp.content
    assert _balance(maker) == 5  # 5 working days reserved while merely PENDING

    # Remaining is 13; ask for a ~3-week span (>13 working days) -> blocked.
    big_start = MONDAY + timedelta(days=7)
    resp2 = _apply(client, approver, big_start, big_start + timedelta(days=20))
    assert resp2.status_code == 400, resp2.content
    assert "exceed" in str(resp2.content).lower()
    assert _balance(maker) == 5  # unchanged — the rejected apply left no trace
    assert Leave.objects.filter(user=maker).count() == 1


@pytest.mark.django_db
def test_dept_head_grant_deducts_exactly_once(maker, checker, approver):
    """M3: pending -> Department Head approve -> APPROVED (granted) keeps the
    deduction at 5 (reserved once while pending, consumed once on grant — never
    doubles). Dept Head approval is final; no HR stage."""
    client = APIClient(); client.force_authenticate(maker)
    resp = _apply(client, approver, MONDAY, FRI)
    leave_id = resp.json()["id"]
    assert _balance(maker) == 5  # reserved while PENDING

    # Department Head approves -> APPROVED immediately (leave granted).
    dh = APIClient(); dh.force_authenticate(checker)
    r1 = dh.post(f"/api/v1/leaves/{leave_id}/dept-head-review/", {"decision": "approve"}, format="json")
    assert r1.status_code == 200, r1.content
    assert Leave.objects.get(pk=leave_id).status == Leave.Status.APPROVED
    assert _balance(maker) == 5  # consumed once, not doubled

    # HR is NOT a second stage: the leave is already approved, so hr-review is rejected.
    hr = APIClient(); hr.force_authenticate(approver)
    r2 = hr.post(f"/api/v1/leaves/{leave_id}/hr-review/", {"decision": "approve"}, format="json")
    assert r2.status_code == 400, r2.content
    assert Leave.objects.get(pk=leave_id).status == Leave.Status.APPROVED
    assert _balance(maker) == 5


@pytest.mark.django_db
def test_set_status_cannot_skip_department_head(maker, approver):
    """M3: set_status may not jump pending -> approved (Level 1 not done)."""
    client = APIClient(); client.force_authenticate(maker)
    leave_id = _apply(client, approver, MONDAY, FRI).json()["id"]

    hr = APIClient(); hr.force_authenticate(approver)
    r = hr.post(f"/api/v1/leaves/{leave_id}/set_status/", {"status": "approved"}, format="json")
    assert r.status_code == 409, r.content
    assert Leave.objects.get(pk=leave_id).status == Leave.Status.PENDING


@pytest.mark.django_db
def test_cancelling_approved_leave_refunds_balance(maker, checker, approver):
    """H3: deleting an approved leave frees the balance it had consumed."""
    client = APIClient(); client.force_authenticate(maker)
    leave_id = _apply(client, approver, MONDAY, FRI).json()["id"]
    # Department Head approval grants the leave (final stage).
    dh = APIClient(); dh.force_authenticate(checker)
    dh.post(f"/api/v1/leaves/{leave_id}/dept-head-review/", {"decision": "approve"}, format="json")
    assert Leave.objects.get(pk=leave_id).status == Leave.Status.APPROVED
    assert _balance(maker) == 5

    # Employee cancels the approved leave -> soft-deleted, balance refunded.
    r = client.delete(f"/api/v1/leaves/{leave_id}/")
    assert r.status_code in (200, 204), r.content
    assert _balance(maker) == 0


@pytest.mark.django_db
def test_hr_fallback_grants_when_no_dept_head(approver):
    """Phase 3 fallback: an employee whose department has NO active Department
    Head can still be granted leave by HR, so a request never gets stuck."""
    solo = _user("solo_emp", User.Roles.MAKER, department="SOLO")  # no checker in SOLO
    client = APIClient(); client.force_authenticate(solo)
    leave_id = _apply(client, approver, MONDAY, FRI).json()["id"]

    hr = APIClient(); hr.force_authenticate(approver)
    r = hr.post(f"/api/v1/leaves/{leave_id}/dept-head-review/", {"decision": "approve"}, format="json")
    assert r.status_code == 200, r.content
    assert Leave.objects.get(pk=leave_id).status == Leave.Status.APPROVED


@pytest.mark.django_db
def test_hr_cannot_grant_when_dept_head_exists(maker, checker, approver):
    """HR is NOT a grant stage when the department HAS an active Department Head:
    the fallback path is refused (403) and the leave stays pending for the DH."""
    client = APIClient(); client.force_authenticate(maker)
    leave_id = _apply(client, approver, MONDAY, FRI).json()["id"]
    hr = APIClient(); hr.force_authenticate(approver)
    r = hr.post(f"/api/v1/leaves/{leave_id}/dept-head-review/", {"decision": "approve"}, format="json")
    assert r.status_code == 403, r.content
    assert Leave.objects.get(pk=leave_id).status == Leave.Status.PENDING
