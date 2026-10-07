"""
End-to-end regression guard for the Phase 1 leave-visibility bug.

The backend was always correct (the approver's queryset returns the submitted
leave); the defect was a frontend pagination-unwrap. These tests lock in the
backend contract the frontend now relies on: a submitted leave is present in the
approver's paginated /leaves/ response with count > 0.
"""
from datetime import date

import pytest
from rest_framework.test import APIClient

from users.models import User


@pytest.fixture
def e2e_users(db):
    # Permanent + tenured so the category engine grants a non-zero Annual balance.
    common = dict(employment_type=User.EmploymentType.PERMANENT, date_of_joining=date(2018, 1, 1))
    maker = User.objects.create_user(
        username="e2e_maker", email="e2e_maker@nif.test", password="pass12345",
        first_name="E2E", last_name="Maker", role=User.Roles.MAKER, department="ENG", **common,
    )
    approver = User.objects.create_user(
        username="e2e_approver", email="e2e_approver@nif.test", password="pass12345",
        role=User.Roles.APPROVER, department="ENG", **common,
    )
    return maker, approver


@pytest.mark.django_db
def test_leave_appears_in_approver_pending_list(e2e_users):
    maker, approver = e2e_users
    client = APIClient()

    # Maker submits a leave application, assigning the approver.
    client.force_authenticate(maker)
    submit = client.post("/api/v1/leaves/", {
        "leave_type": "annual", "start_date": "2026-06-15", "end_date": "2026-06-16",
        "reason": "E2E visibility test", "approver": str(approver.id),
    }, format="json")
    assert submit.status_code == 201, submit.data
    leave_id = submit.data["id"]
    assert submit.data["status"] == "pending"
    assert str(submit.data["approver"]) == str(approver.id)

    # Approver's list is paginated; the submitted leave must be present.
    client.force_authenticate(approver)
    listed = client.get("/api/v1/leaves/")
    assert listed.status_code == 200
    assert set(["count", "results"]).issubset(listed.data.keys())  # DRF paginated
    assert listed.data["count"] >= 1
    ids = [row["id"] for row in listed.data["results"]]
    assert leave_id in ids
    assert any(row["status"] == "pending" for row in listed.data["results"])


@pytest.mark.django_db
def test_maker_sees_own_submitted_leave(e2e_users):
    maker, approver = e2e_users
    client = APIClient()
    client.force_authenticate(maker)
    client.post("/api/v1/leaves/", {
        "leave_type": "annual", "start_date": "2026-06-20", "end_date": "2026-06-21",
        "reason": "own visibility", "approver": str(approver.id),
    }, format="json")

    listed = client.get("/api/v1/leaves/")
    assert listed.data["count"] == 1
    assert str(listed.data["results"][0]["user"]) == str(maker.id)
