import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from memos.models import Memo
from memos.serializers import (
    MemoCreateSerializer,
    MemoDetailSerializer,
    MemoWorkflowActionSerializer,
    MAX_ATTACHMENT_SIZE,
)


@pytest.mark.django_db
def test_create_serializer_accepts_valid_payload():
    serializer = MemoCreateSerializer(data={
        "subject": "Subject",
        "memo_type": Memo.MemoType.GENERAL,
    })
    assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
def test_create_serializer_rejects_oversized_attachment():
    big = SimpleUploadedFile(
        "big.pdf", b"x" * (MAX_ATTACHMENT_SIZE + 1), content_type="application/pdf"
    )
    serializer = MemoCreateSerializer(data={ "subject": "S", "memo_type": Memo.MemoType.GENERAL, "attachment": big,
    })
    assert not serializer.is_valid()
    assert "attachment" in serializer.errors


@pytest.mark.django_db
def test_create_serializer_rejects_disallowed_extension():
    bad = SimpleUploadedFile("virus.exe", b"data", content_type="application/octet-stream")
    serializer = MemoCreateSerializer(data={ "subject": "S", "memo_type": Memo.MemoType.GENERAL, "attachment": bad,
    })
    assert not serializer.is_valid()
    assert "attachment" in serializer.errors


@pytest.mark.django_db
def test_create_serializer_accepts_allowed_extension():
    ok = SimpleUploadedFile("doc.pdf", b"%PDF-1.4 data", content_type="application/pdf")
    serializer = MemoCreateSerializer(data={ "subject": "S", "memo_type": Memo.MemoType.GENERAL, "attachment": ok,
    })
    assert serializer.is_valid(), serializer.errors


# MemoActionSerializer, which validated the legacy action endpoints' bodies, was
# removed with them. MemoWorkflowActionSerializer is its replacement; the minimum
# remark length for a rejection is now enforced by the engine rather than the
# serializer, because it depends on the active step's role type.
def test_workflow_action_serializer_requires_a_known_decision():
    serializer = MemoWorkflowActionSerializer(data={"decision": "approve"})
    assert not serializer.is_valid()
    assert "decision" in serializer.errors


def test_workflow_action_serializer_accepts_proceed_and_reject():
    for decision in ("proceed", "reject"):
        serializer = MemoWorkflowActionSerializer(
            data={"decision": decision, "remarks": "Checked and in order."})
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["decision"] == decision


def test_workflow_action_serializer_defaults_remarks_to_empty():
    serializer = MemoWorkflowActionSerializer(data={"decision": "proceed"})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["remarks"] == ""


@pytest.mark.django_db
def test_detail_serializer_capability_flags_for_author(draft_memo, maker):
    class _Req:
        user = None

    req = _Req()
    req.user = maker
    data = MemoDetailSerializer(draft_memo, context={"request": req}).data
    assert data["can_edit"] is True
    # A draft with no matrix yet: the author may build one, but there is nobody to
    # send it to, so send-for-review is not offered.
    assert data["can_edit_matrix"] is True
    assert data["can_send_for_review"] is False
    assert data["can_act"] is False
    assert data["can_delete"] is True
    assert data["status_label"] == "Draft"
    assert data["created_by"]["email"] == maker.email
    # list-style nested user should not leak password or full model
    # (employee_id added for the detailed-review panel: "Created By ... + employee ID").
    assert set(data["created_by"].keys()) == {"id", "full_name", "email", "role", "department", "employee_id"}
