"""
Phase 111.4 guard.

The autosave feature was approved on one explicit condition: no "Auto Saved
Draft" status, and the three modules' workflow ladders left exactly as they
were. Autosave state lives on the draft snapshot and in the UI indicator, never
in a document's status field.

These tests fail loudly if a later change starts to blur that line — which is
the failure mode worth guarding, because it would leak an autosave concept into
every dashboard tile, filter, permission check and PDF in the system.
"""
import pytest

from circulars.models import Circular
from memos.models import Memo
from minutes.models import Minute

MEMO_STATUSES = {
    "draft", "draft_for_review", "under_review", "recommended",
    "supported", "approved", "rejected", "archived", "cancelled",
}
MINUTE_STATUSES = {
    "draft", "draft_for_review", "pending_acknowledgement", "archived",
}
CIRCULAR_STATUSES = {
    "draft", "under_review", "ready_for_issue", "issued",
    "ready_for_broadcast", "broadcasted", "archived", "rejected", "cancelled",
}


@pytest.mark.parametrize("model,expected", [
    (Memo, MEMO_STATUSES),
    (Minute, MINUTE_STATUSES),
    (Circular, CIRCULAR_STATUSES),
])
def test_the_status_ladder_is_unchanged(model, expected):
    assert {value for value, _ in model.Status.choices} == expected


@pytest.mark.parametrize("model", [Memo, Minute, Circular])
def test_no_autosave_status_was_introduced(model):
    values = " ".join(value for value, _ in model.Status.choices)
    assert "auto" not in values.lower()


@pytest.mark.parametrize("model", [Memo, Minute, Circular])
def test_the_draft_store_adds_no_field_to_the_document(model):
    """
    Autosave state is a property of the snapshot, not of the document. If a
    column like `autosave_state` ever appears here, the separation the feature
    was approved on has been lost.
    """
    names = {field.name for field in model._meta.get_fields()}
    assert not {n for n in names if "autosave" in n or "auto_saved" in n}
