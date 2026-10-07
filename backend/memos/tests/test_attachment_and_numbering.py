"""
H3: attachment content-type sniffing (magic bytes, not just filename).
H4: race-safe, gap-free memo numbering via the counter table.
"""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from memos.models import Memo, MemoNumberSequence
from memos.services import generate_memo_number


# --- H3: content sniffing ---------------------------------------------------
def _upload(api, user, upload):
    api.force_authenticate(user)
    return api.post(
        "/api/v1/memos/",
        {"subject": "S", "memo_type": "general", "attachment": upload},
        format="multipart",
    )


@pytest.mark.django_db
def test_renamed_html_as_pdf_rejected(api, maker):
    evil = SimpleUploadedFile("evil.pdf", b"<html><script>alert(1)</script></html>",
                              content_type="application/pdf")
    resp = _upload(api, maker, evil)
    assert resp.status_code == 400
    assert "attachment" in resp.data


@pytest.mark.django_db
def test_valid_pdf_accepted(api, maker):
    good = SimpleUploadedFile("real.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n",
                              content_type="application/pdf")
    resp = _upload(api, maker, good)
    assert resp.status_code == 201, resp.data


@pytest.mark.django_db
def test_an_image_is_accepted(api, maker):
    """
    Images ARE accepted on memos (Phase ATTACHMENT-POLICY-FINAL).

    THIS TEST HAS NOW BEEN INVERTED TWICE, so the history matters more than the
    assertion:

      * originally a PNG was accepted;
      * it was refused when the rule was tightened to the six types the E-memo
        manual names on p.6 — "accept only PDF, doc, docx, xls, xlsx, csv" — and
        this test was inverted to hold that line;
      * it is accepted again by explicit instruction, memo, minute and circular
        now sharing one policy (config.uploads.DOCUMENT_ATTACHMENT_EXTENSIONS).

    So the code and the manual currently DISAGREE. p.6 needs updating; until it
    is, anyone reading the manual will file this behaviour as a defect. That is
    a documentation task, not a code one, and it is recorded here because this
    test is where the last person looked.
    """
    png = SimpleUploadedFile(
        "img.png",
        bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000"
                      "001f15c4890000000a49444154789c636000000200010005fe02fea7"
                      "b1a4b40000000049454e44ae426082"),
        content_type="image/png",
    )
    api.force_authenticate(maker)
    resp = api.post("/api/v1/memos/",
                    {"subject": "S", "memo_type": "general", "attachment": png},
                    format="multipart")
    assert resp.status_code == 201, resp.data


# --- H4: race-safe numbering ------------------------------------------------
@pytest.mark.django_db
def test_sequential_numbers_are_unique_and_gap_free(maker):
    numbers = [generate_memo_number(Memo.MemoType.GENERAL) for _ in range(5)]
    seqs = [int(n.rsplit("-", 1)[-1]) for n in numbers]
    assert seqs == sorted(seqs)
    assert len(set(numbers)) == 5  # all unique


@pytest.mark.django_db
def test_first_memo_of_year_has_no_race_on_empty_table(maker):
    # No counter row and no memos yet: the first call must succeed and start at 1.
    assert not MemoNumberSequence.objects.exists()
    first = generate_memo_number(Memo.MemoType.CONFIDENTIAL)
    assert first.endswith("-0001")


@pytest.mark.django_db
def test_sequence_beyond_9999_increments_correctly(maker):
    from django.utils import timezone
    year = timezone.now().year
    MemoNumberSequence.objects.create(type_code="GEN", year=year, last_value=9999)
    n1 = generate_memo_number(Memo.MemoType.GENERAL)
    n2 = generate_memo_number(Memo.MemoType.GENERAL)
    assert n1.endswith("-10000")
    assert n2.endswith("-10001")
    # The integer counter, not a lexical sort, drives ordering (no 10000<9999 bug).
    assert int(n2.rsplit("-", 1)[-1]) > int(n1.rsplit("-", 1)[-1])
