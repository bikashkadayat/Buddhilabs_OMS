"""
memos.services after the legacy engine was removed: memo numbering, the audit
helper and the history-row writer.

The workflow-transition tests that used to live here moved to
test_workflow_matrix.py, which exercises the same guarantees (atomicity, guard
failures, history + audit rows per transition) against the single engine that
now performs them.
"""
import pytest
from rest_framework.exceptions import ValidationError

from audit.models import AuditLog
from memos.models import Memo, MemoApprovalStep
from memos import services
from users.models import User


def _a_user(username="numgen"):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        role=User.Roles.MAKER, department="Finance",
    )


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_generate_memo_number_format_and_increment():
    first = services.generate_memo_number(Memo.MemoType.CONFIDENTIAL)
    assert first.startswith("NIFN-CON-")
    assert first.endswith("-0001")

    Memo.objects.create(
        subject="s", memo_type=Memo.MemoType.CONFIDENTIAL,
        created_by=_a_user(), memo_number=first,
    )
    second = services.generate_memo_number(Memo.MemoType.CONFIDENTIAL)
    assert second.endswith("-0002")


@pytest.mark.django_db
def test_numbering_is_scoped_per_type():
    """Each type code keeps its own counter, so HR-0001 and GEN-0001 coexist."""
    assert services.generate_memo_number(Memo.MemoType.CONFIDENTIAL).endswith("-0001")
    assert services.generate_memo_number(Memo.MemoType.GENERAL).endswith("-0001")
    assert services.generate_memo_number(Memo.MemoType.CONFIDENTIAL).endswith("-0002")


# ---------------------------------------------------------------------------
# History rows
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_record_step_appends_in_order(draft_memo, maker):
    # The fixture already carries the "Created" row, as a real memo does.
    assert list(draft_memo.approval_steps.values_list("action", flat=True)) == ["created"]

    services._record_step(draft_memo, maker, MemoApprovalStep.Action.SENT_FOR_REVIEW)
    services._record_step(draft_memo, maker, MemoApprovalStep.Action.REVIEWED)

    rows = list(draft_memo.approval_steps.order_by("step_order")
                .values_list("step_order", "action"))
    assert rows == [(1, "created"), (2, "sent_for_review"), (3, "reviewed")]


@pytest.mark.django_db
def test_require_comment_enforces_minimum_length():
    with pytest.raises(ValidationError):
        services._require_comment("too short")
    assert services._require_comment("  a long enough reason  ") == "a long enough reason"


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_create_audit_log_records_actor_action_and_metadata(draft_memo, maker):
    services.create_audit_log(
        maker, AuditLog.Action.CREATE, instance=draft_memo,
        metadata={"transition": "created", "remarks": "first draft"},
    )
    entry = AuditLog.objects.filter(object_id=str(draft_memo.id)).first()
    assert entry is not None
    assert entry.actor_id == maker.id
    assert entry.action == AuditLog.Action.CREATE
    assert entry.changes["transition"] == "created"
    assert entry.changes["remarks"] == "first draft"


@pytest.mark.django_db
def test_audit_rows_are_immutable(draft_memo, maker):
    services.create_audit_log(maker, AuditLog.Action.CREATE, instance=draft_memo)
    entry = AuditLog.objects.filter(object_id=str(draft_memo.id)).first()
    entry.action = AuditLog.Action.DELETE
    with pytest.raises(ValueError):
        entry.save()
    with pytest.raises(ValueError):
        entry.delete()


@pytest.mark.django_db
def test_legacy_transition_functions_are_gone():
    """
    Guards the removal itself. If a two-slot transition is ever reintroduced here
    the module has two workflow engines again, which is exactly the state Phase 12
    set out to end - so this failing is the signal, not the problem.
    """
    for name in ("submit_memo", "review_memo", "approve_memo", "reject_memo",
                 "return_memo", "cancel_memo", "resolve_next_reviewer",
                 "resolve_next_approver"):
        assert not hasattr(services, name), f"services.{name} should have been removed"


@pytest.mark.django_db
def test_memo_has_no_legacy_routing_columns():
    field_names = {field.name for field in Memo._meta.get_fields()}
    assert "current_reviewer" not in field_names
    assert "current_approver" not in field_names
