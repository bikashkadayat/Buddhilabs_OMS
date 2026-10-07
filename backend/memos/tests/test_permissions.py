import pytest

from memos.models import Memo

from .conftest import act, route


@pytest.mark.django_db
def test_maker_cannot_see_others_memo(api, draft_memo, other_maker):
    api.force_authenticate(other_maker)
    resp = api.get(f"/api/v1/memos/{draft_memo.id}/")
    # not in queryset -> 404 (object filtered out before object-perm check)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_author_can_see_own_memo(api, draft_memo, maker):
    api.force_authenticate(maker)
    resp = api.get(f"/api/v1/memos/{draft_memo.id}/")
    assert resp.status_code == 200
    assert resp.data["memo_number"] == draft_memo.memo_number


@pytest.mark.django_db
def test_admin_sees_everything(api, draft_memo, admin):
    api.force_authenticate(admin)
    resp = api.get(f"/api/v1/memos/{draft_memo.id}/")
    assert resp.status_code == 200


@pytest.mark.django_db
def test_being_routed_on_a_memo_grants_read(api, draft_memo, maker, checker, approver):
    """Membership of the approval matrix is what grants access, at any position."""
    route(api, maker, draft_memo, (checker, "reviewer"), (approver, "approver"))
    api.force_authenticate(checker)
    assert api.get(f"/api/v1/memos/{draft_memo.id}/").status_code == 200


@pytest.mark.django_db
def test_author_cannot_act_on_their_own_memo(api, draft_memo, maker, checker, approver):
    """The author is never on their own chain, so they have no step to action."""
    route(api, maker, draft_memo, (checker, "reviewer"), (approver, "approver"))
    resp = act(api, maker, draft_memo)
    assert resp.status_code in (403, 404)
    draft_memo.refresh_from_db()
    assert draft_memo.status == Memo.Status.DRAFT_FOR_REVIEW


@pytest.mark.django_db
def test_someone_not_on_the_chain_cannot_act(api, draft_memo, maker, checker,
                                             other_checker, approver):
    route(api, maker, draft_memo, (checker, "reviewer"), (approver, "approver"))
    resp = act(api, other_checker, draft_memo, remarks="Trying to hijack this.")
    # Holding the same role as the assignee grants nothing - only the step does.
    assert resp.status_code in (403, 404)
    draft_memo.refresh_from_db()
    assert draft_memo.status == Memo.Status.DRAFT_FOR_REVIEW


@pytest.mark.django_db
def test_non_author_cannot_send_a_memo_for_review(api, draft_memo, maker, approver, checker):
    """
    Sending for review is author-gated. HR has an org-wide read, so the memo is
    inside their queryset and get_object() succeeds - the engine's author check is
    what refuses. What matters is that the memo does not move.
    """
    api.force_authenticate(approver)
    resp = api.post(f"/api/v1/memos/{draft_memo.id}/send-for-review/", {"workflow": [
        {"assignee_id": str(checker.id), "role_type": "reviewer"},
        {"assignee_id": str(approver.id), "role_type": "approver"},
    ]}, format="json")
    assert resp.status_code in (400, 403, 404)
    draft_memo.refresh_from_db()
    assert draft_memo.status == Memo.Status.DRAFT


@pytest.mark.django_db
def test_non_author_cannot_set_the_matrix(api, draft_memo, other_maker, checker, approver):
    api.force_authenticate(other_maker)
    resp = api.post(f"/api/v1/memos/{draft_memo.id}/matrix/", {"workflow": [
        {"assignee_id": str(checker.id), "role_type": "reviewer"},
        {"assignee_id": str(approver.id), "role_type": "approver"},
    ]}, format="json")
    assert resp.status_code in (400, 403, 404)
    assert not draft_memo.workflow_steps.exists()


@pytest.mark.django_db
def test_unauthenticated_denied(api, draft_memo):
    resp = api.get(f"/api/v1/memos/{draft_memo.id}/")
    assert resp.status_code == 401
