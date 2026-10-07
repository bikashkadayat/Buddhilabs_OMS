"""H2: a single canonical generator yields the typed format on every path."""
import re

import pytest

from memos.models import Memo
from users.models import User

TYPED = re.compile(r"^NIFN-(GEN|CON|DRF)-\d{4}-\d+$")


@pytest.mark.django_db
def test_memo_number_typed_format_via_api(api, maker):
    api.force_authenticate(maker)
    resp = api.post("/api/v1/memos/",
                    {"subject": "S", "memo_type": "general"}, format="json")
    assert resp.status_code == 201, resp.data
    assert resp.data["memo_number"].startswith("NIFN-GEN-")
    assert TYPED.match(resp.data["memo_number"])


@pytest.mark.django_db
def test_memo_number_typed_format_via_orm(maker):
    # The "shell"/admin path: create without a number -> Memo.save() assigns it.
    memo = Memo.objects.create(subject="S",
                               memo_type=Memo.MemoType.CONFIDENTIAL, created_by=maker)
    assert memo.memo_number.startswith("NIFN-CON-")
    assert TYPED.match(memo.memo_number)


@pytest.mark.django_db
def test_no_legacy_memo_prefix_generated(maker):
    memo = Memo.objects.create(subject="S",
                               memo_type=Memo.MemoType.GENERAL, created_by=maker)
    # The old divergent generator produced "NIFN-MEMO-..."; it must be gone.
    assert "NIFN-MEMO-" not in memo.memo_number
