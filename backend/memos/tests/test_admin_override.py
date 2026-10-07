"""H1: an admin may act on any memo on behalf of the pending assignee, and the
override is flagged in the audit trail rather than passing silently."""
import pytest

from audit.models import AuditLog
from memos.models import Memo

from .conftest import act, route


@pytest.mark.django_db
def test_admin_can_act_at_the_reviewer_step(api, maker, checker, approver, admin):
    memo = route(api, maker, __memo(maker), (checker, "reviewer"), (approver, "approver"))
    resp = act(api, admin, memo, remarks="Actioned on behalf of the reviewer.")
    assert resp.status_code == 200, resp.data

    memo.refresh_from_db()
    assert memo.status == Memo.Status.UNDER_REVIEW
    # The step records the assignee it belonged to; the ACTOR is in the audit log.
    assert memo.workflow_steps.get(assignee=checker).status == "completed"


@pytest.mark.django_db
def test_admin_can_approve_at_the_final_step(api, maker, checker, approver, admin):
    memo = route(api, maker, __memo(maker), (checker, "reviewer"), (approver, "approver"))
    act(api, checker, memo, remarks="Reviewed and correct.")

    resp = act(api, admin, memo, remarks="Approved on behalf of HR.")
    assert resp.status_code == 200, resp.data
    memo.refresh_from_db()
    assert memo.status == Memo.Status.ARCHIVED


@pytest.mark.django_db
def test_admin_can_reject_any_memo(api, maker, checker, approver, admin):
    memo = route(api, maker, __memo(maker), (checker, "reviewer"), (approver, "approver"))
    resp = act(api, admin, memo, decision="reject",
               remarks="Not acceptable in this form at all.")
    assert resp.status_code == 200, resp.data
    memo.refresh_from_db()
    assert memo.status == Memo.Status.REJECTED


@pytest.mark.django_db
def test_admin_override_writes_an_audit_row_flagged_as_such(api, maker, checker,
                                                            approver, admin):
    memo = route(api, maker, __memo(maker), (checker, "reviewer"), (approver, "approver"))
    act(api, admin, memo, remarks="Actioned on behalf of the reviewer.")

    entry = AuditLog.objects.filter(
        object_id=str(memo.id), actor=admin, changes__transition="reviewed").first()
    assert entry is not None
    assert entry.changes["admin_override"] is True
    assert entry.changes["remarks"] == "Actioned on behalf of the reviewer."


@pytest.mark.django_db
def test_the_assignee_acting_normally_is_not_flagged_as_an_override(api, maker, checker,
                                                                   approver):
    memo = route(api, maker, __memo(maker), (checker, "reviewer"), (approver, "approver"))
    act(api, checker, memo, remarks="Reviewed in the normal course.")

    entry = AuditLog.objects.filter(
        object_id=str(memo.id), actor=checker, changes__transition="reviewed").first()
    assert entry is not None
    assert entry.changes["admin_override"] is False


@pytest.mark.django_db
def test_admin_still_cannot_act_out_of_sequence(api, maker, checker, approver, admin):
    """
    The override lets an admin act *for* the pending assignee. It does not let
    them skip a step - the memo would then be approved with a control point
    outstanding, which is the thing sequential enforcement exists to prevent.
    """
    memo = route(api, maker, __memo(maker), (checker, "reviewer"), (approver, "approver"))
    # The active step is the reviewer's, so an admin acting resolves to THAT step,
    # never to the approver's further down the chain.
    act(api, admin, memo, remarks="Acting for the reviewer here.")
    memo.refresh_from_db()
    assert memo.status == Memo.Status.UNDER_REVIEW
    assert memo.workflow_steps.get(assignee=approver).status == "active"


def __memo(author):
    from memos.services import generate_memo_number
    return Memo.objects.create(
        subject="S",
        memo_type=Memo.MemoType.GENERAL, status=Memo.Status.DRAFT,
        created_by=author, memo_number=generate_memo_number(Memo.MemoType.GENERAL),
    )
