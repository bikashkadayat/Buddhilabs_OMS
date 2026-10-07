"""M2: create-and-submit is atomic - a failed send leaves no orphaned draft."""
import pytest

from memos.models import Memo

from .conftest import matrix


def _payload():
    return {"subject": "S", "memo_type": "general", "priority": "normal"}


@pytest.mark.django_db
def test_create_and_submit_requires_a_workflow(api, maker):
    """
    There is no auto-routing fallback any more: the person who knows who should
    approve a memo is the person writing it, so a payload with no matrix is a
    400 rather than a memo routed to whoever the system guessed.
    """
    api.force_authenticate(maker)
    before = Memo.objects.count()
    resp = api.post("/api/v1/memos/create-and-submit/", _payload(), format="json")
    assert resp.status_code == 400
    assert Memo.objects.count() == before


@pytest.mark.django_db
def test_create_and_submit_rolls_back_on_an_invalid_workflow(api, maker, checker):
    """A structurally invalid chain must not leave the draft behind."""
    api.force_authenticate(maker)
    before = Memo.objects.count()
    payload = {**_payload(), "workflow": matrix((checker, "reviewer"))}
    resp = api.post("/api/v1/memos/create-and-submit/", payload, format="json")

    assert resp.status_code == 400
    assert "final step must be an Approver" in str(resp.data)
    assert Memo.objects.count() == before  # rolled back, no orphan draft


@pytest.mark.django_db
def test_create_and_submit_rolls_back_when_an_assignee_is_invalid(api, maker, approver):
    api.force_authenticate(maker)
    before = Memo.objects.count()
    payload = {**_payload(),
               "workflow": matrix((maker, "reviewer"), (approver, "approver"))}
    resp = api.post("/api/v1/memos/create-and-submit/", payload, format="json")

    assert resp.status_code == 400
    assert "your own approval workflow" in str(resp.data)
    assert Memo.objects.count() == before


@pytest.mark.django_db
def test_create_and_submit_succeeds_with_a_valid_workflow(api, maker, checker, approver):
    api.force_authenticate(maker)
    payload = {**_payload(),
               "workflow": matrix((checker, "reviewer"), (approver, "approver"))}
    resp = api.post("/api/v1/memos/create-and-submit/", payload, format="json")

    assert resp.status_code == 201, resp.data
    assert resp.data["status"] == "draft_for_review"
    assert resp.data["pending_with"]["id"] == str(checker.id)
    assert resp.data["pending_with"]["role_label"] == "Reviewer"
    # Creation and submission are both on the timeline, in that order.
    assert [s["action"] for s in resp.data["approval_steps"]] == [
        "created", "sent_for_review"]


@pytest.mark.django_db
def test_create_and_submit_accepts_the_matrix_as_a_json_string(api, maker, checker, approver):
    """
    multipart/form-data cannot carry a nested array, so the create form sends the
    matrix as a JSON string alongside any file parts. Both shapes must work.
    """
    import json

    api.force_authenticate(maker)
    payload = {**_payload(),
               "workflow": json.dumps(matrix((checker, "reviewer"), (approver, "approver")))}
    resp = api.post("/api/v1/memos/create-and-submit/", payload, format="multipart")

    assert resp.status_code == 201, resp.data
    assert resp.data["pending_with"]["id"] == str(checker.id)
