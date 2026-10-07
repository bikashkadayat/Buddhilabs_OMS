import pytest

from audit.models import AuditLog
from memos.models import Memo, MemoApprovalStep

from .conftest import act, route


@pytest.mark.django_db
def test_create_draft(api, maker):
    api.force_authenticate(maker)
    resp = api.post("/api/v1/memos/", {
        "subject": "Subject",
        "memo_type": Memo.MemoType.GENERAL,
    })
    assert resp.status_code == 201, resp.data
    memo = Memo.objects.get()
    assert memo.status == Memo.Status.DRAFT
    assert memo.created_by_id == maker.id
    assert memo.memo_number.startswith("NIFN-GEN-")
    assert AuditLog.objects.filter(action=AuditLog.Action.CREATE, object_id=str(memo.id)).exists()
    # The timeline opens at Created, not at the first workflow action.
    assert memo.approval_steps.filter(action=MemoApprovalStep.Action.CREATED).exists()


@pytest.mark.django_db
def test_end_to_end_api_workflow(api, draft_memo, maker, checker, approver):
    route(api, maker, draft_memo, (checker, "reviewer"), (approver, "approver"))
    draft_memo.refresh_from_db()
    assert draft_memo.status == Memo.Status.DRAFT_FOR_REVIEW

    r = act(api, checker, draft_memo, remarks="Reviewed and forwarded.")
    assert r.status_code == 200, r.data
    assert r.data["status"] == Memo.Status.UNDER_REVIEW

    r = act(api, approver, draft_memo, remarks="Approved; figures verified.")
    assert r.status_code == 200, r.data
    # Approval archives in the same transaction.
    assert r.data["status"] == Memo.Status.ARCHIVED

    actions = [s["action"] for s in r.data["approval_steps"]]
    assert actions == ["created", "sent_for_review", "reviewed", "approved", "archived"]


@pytest.mark.django_db
def test_reject_via_api_requires_a_reason(api, draft_memo, maker, checker, approver):
    route(api, maker, draft_memo, (checker, "reviewer"), (approver, "approver"))

    too_short = act(api, checker, draft_memo, decision="reject", remarks="no")
    assert too_short.status_code == 400

    ok = act(api, checker, draft_memo, decision="reject",
             remarks="Rejected: incomplete documentation.")
    assert ok.status_code == 200
    assert ok.data["status"] == Memo.Status.REJECTED


@pytest.mark.django_db
def test_list_uses_light_serializer(api, draft_memo, maker):
    api.force_authenticate(maker)
    r = api.get("/api/v1/memos/")
    assert r.status_code == 200
    row = r.data["results"][0]
    # The list payload must not carry the body or the raw attachment path.
    assert "body" not in row
    assert "attachment" not in row
    assert "memo_number" in row


@pytest.mark.django_db
def test_list_is_paginated(api, maker):
    api.force_authenticate(maker)
    r = api.get("/api/v1/memos/")
    assert r.status_code == 200
    assert {"count", "next", "previous", "results"}.issubset(r.data.keys())


@pytest.mark.django_db
def test_templates_endpoint_returns_active_only(api, maker):
    from memos.models import MemoTemplate
    MemoTemplate.objects.create(name="Active", memo_type=Memo.MemoType.CONFIDENTIAL,
                                subject_template="s", body_template="b", is_active=True)
    MemoTemplate.objects.create(name="Inactive", memo_type=Memo.MemoType.CONFIDENTIAL,
                                subject_template="s", body_template="b", is_active=False)
    api.force_authenticate(maker)
    r = api.get("/api/v1/memo-templates/")
    assert r.status_code == 200
    names = [t["name"] for t in r.data["results"]]
    assert "Active" in names and "Inactive" not in names


@pytest.mark.django_db
@pytest.mark.parametrize("path", [
    "submit", "review", "approve", "reject", "return",
])
def test_legacy_workflow_endpoints_are_gone(api, draft_memo, maker, path):
    """
    The two-slot endpoints were removed with the engine behind them. A 404 here
    (rather than a 405 or a 500) is what tells us the route no longer exists.
    """
    api.force_authenticate(maker)
    assert api.post(f"/api/v1/memos/{draft_memo.id}/{path}/", {}, format="json").status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["available-checkers", "available-approvers"])
def test_legacy_assignee_pickers_are_gone(api, maker, path):
    """Replaced by /memos/employees/, which is not restricted by role."""
    api.force_authenticate(maker)
    assert api.get(f"/api/v1/memos/{path}/", {"search": "ch"}).status_code == 404
