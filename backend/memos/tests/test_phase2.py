"""
Phase 2: universal memo creation, and routing chosen by the author.

The "hybrid checker assignment" tests that used to fill the second half of this
file exercised the legacy /submit/ endpoint and the two role-filtered pickers.
Their guarantees - the author picks the routing, an invalid or self-assigned
assignee is a 400 - are asserted here against the approval matrix instead.
"""
import pytest

from memos.models import Memo
from users.models import User


def _payload():
    return {"subject": "S", "memo_type": "general", "priority": "normal"}


def _draft_by(api, user):
    api.force_authenticate(user)
    resp = api.post("/api/v1/memos/", _payload(), format="json")
    assert resp.status_code == 201, resp.data
    return resp.data["id"]


# --- STEP 2.2: universal creation ------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("role_fixture", ["maker", "checker", "approver"])
def test_non_admin_users_can_create_memo(api, request, role_fixture):
    # Employee / Department Head / HR may author memos. Admin is excluded below.
    user = request.getfixturevalue(role_fixture)
    api.force_authenticate(user)
    resp = api.post("/api/v1/memos/", _payload(), format="json")
    assert resp.status_code == 201, resp.data
    assert Memo.objects.get(id=resp.data["id"]).created_by_id == user.id


@pytest.mark.django_db
def test_admin_cannot_create_memo(api, admin):
    # Admin is an oversight/approval role and does not author memo requests.
    api.force_authenticate(admin)
    resp = api.post("/api/v1/memos/", _payload(), format="json")
    assert resp.status_code == 403, resp.data


@pytest.mark.django_db
def test_unauthenticated_cannot_create_memo(api):
    assert api.post("/api/v1/memos/", _payload(), format="json").status_code == 401


# --- routing is chosen by the author --------------------------------------
@pytest.mark.django_db
def test_author_routes_the_memo_to_the_people_they_choose(api, maker, checker,
                                                          other_checker, approver):
    mid = _draft_by(api, maker)
    api.force_authenticate(maker)
    resp = api.post(f"/api/v1/memos/{mid}/send-for-review/", {"workflow": [
        {"assignee_id": str(other_checker.id), "role_type": "reviewer"},
        {"assignee_id": str(approver.id), "role_type": "approver"},
    ]}, format="json")

    assert resp.status_code == 200, resp.data
    assert resp.data["pending_with"]["id"] == str(other_checker.id)
    # `checker` was never named, so they are not on the chain at all.
    assert str(checker.id) not in {
        step["assignee"]["id"] for step in resp.data["workflow_steps"]}


@pytest.mark.django_db
def test_any_employee_may_hold_any_role_in_the_chain(api, maker, other_maker, approver):
    """
    A plain Employee was not an eligible checker under the old rules. Phase 4
    removes that restriction: role types are matrix attributes, not user roles.
    """
    mid = _draft_by(api, maker)
    api.force_authenticate(maker)
    resp = api.post(f"/api/v1/memos/{mid}/send-for-review/", {"workflow": [
        {"assignee_id": str(other_maker.id), "role_type": "reviewer"},
        {"assignee_id": str(approver.id), "role_type": "approver"},
    ]}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["pending_with"]["id"] == str(other_maker.id)


@pytest.mark.django_db
def test_author_cannot_route_a_memo_to_themselves(api, checker, approver):
    mid = _draft_by(api, checker)
    api.force_authenticate(checker)
    resp = api.post(f"/api/v1/memos/{mid}/send-for-review/", {"workflow": [
        {"assignee_id": str(checker.id), "role_type": "reviewer"},
        {"assignee_id": str(approver.id), "role_type": "approver"},
    ]}, format="json")
    assert resp.status_code == 400
    assert "your own approval workflow" in str(resp.data)


@pytest.mark.django_db
def test_an_inactive_employee_cannot_be_routed_to(api, maker, approver):
    inactive = User.objects.create_user(
        username="inactive_chk", email="ic@nif.test", password="pass12345",
        role=User.Roles.CHECKER, is_active=False, first_name="Inactive",
    )
    mid = _draft_by(api, maker)
    api.force_authenticate(maker)
    resp = api.post(f"/api/v1/memos/{mid}/send-for-review/", {"workflow": [
        {"assignee_id": str(inactive.id), "role_type": "reviewer"},
        {"assignee_id": str(approver.id), "role_type": "approver"},
    ]}, format="json")
    assert resp.status_code == 400
    assert "not an active employee" in str(resp.data)


@pytest.mark.django_db
def test_a_memo_cannot_be_sent_without_a_workflow(api, maker):
    mid = _draft_by(api, maker)
    api.force_authenticate(maker)
    resp = api.post(f"/api/v1/memos/{mid}/send-for-review/", {}, format="json")
    assert resp.status_code == 400
    assert "at least one approver" in str(resp.data)
