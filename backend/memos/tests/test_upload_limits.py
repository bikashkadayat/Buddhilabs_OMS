"""
Phase MEMO-P1-PRODUCTION-BLOCKERS: batch upload ceilings.

The per-file 10MB cap was the only bound on an upload, so ten files at the
limit made a 94MB request that the server accepted and stored. These pin the
count, per-memo and total-size rules, and the fact that each refusal SAYS what
the limit is -- a two-minute upload rejected with "upload failed" is not a
usable answer.
"""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from memos import services
from memos.models import Memo

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"


def _pdf(name, size=1024):
    return SimpleUploadedFile(name, PDF + b"\0" * max(size - len(PDF), 0),
                              content_type="application/pdf")


@pytest.fixture
def memo(maker):
    return Memo.objects.create(subject="S", memo_type="general", created_by=maker)


def _post(api, user, memo, files):
    api.force_authenticate(user)
    return api.post(f"/api/v1/memos/{memo.id}/attachments/",
                    {"files": files}, format="multipart")


@pytest.mark.django_db
def test_a_normal_batch_is_accepted(api, maker, memo):
    resp = _post(api, maker, memo, [_pdf(f"q{i}.pdf") for i in range(5)])
    assert resp.status_code == 201, resp.data
    assert memo.attachments.count() == 5


@pytest.mark.django_db
def test_the_per_request_count_is_the_boundary(api, maker, memo):
    limit = services.MAX_MEMO_ATTACHMENTS_PER_REQUEST
    ok = _post(api, maker, memo, [_pdf(f"q{i}.pdf") for i in range(limit)])
    assert ok.status_code == 201, ok.data

    memo.attachments.all().delete()
    too_many = _post(api, maker, memo, [_pdf(f"q{i}.pdf") for i in range(limit + 1)])
    assert too_many.status_code == 400
    message = too_many.data["files"][0]
    assert str(limit) in message and str(limit + 1) in message
    # Nothing was written: the batch is refused before any single file is stored.
    assert memo.attachments.count() == 0


@pytest.mark.django_db
def test_total_size_is_capped_even_when_every_file_is_legal(api, maker, memo):
    """
    THE 94MB CASE. Each file is inside the 10MB per-file limit, so the old
    per-file check passed all ten and the request was accepted whole.
    """
    # Sized so each file is comfortably legal on its own but the batch is not:
    # the point of the test is that per-file validation cannot catch this.
    per_file = int(services.MAX_MEMO_ATTACHMENT_SIZE * 0.9)
    count = services.MAX_MEMO_UPLOAD_TOTAL_SIZE // per_file + 1
    files = [_pdf(f"big{i}.pdf", per_file) for i in range(count)]
    assert all(f.size <= services.MAX_MEMO_ATTACHMENT_SIZE for f in files)
    assert sum(f.size for f in files) > services.MAX_MEMO_UPLOAD_TOTAL_SIZE
    assert count <= services.MAX_MEMO_ATTACHMENTS_PER_REQUEST

    resp = _post(api, maker, memo, files)
    assert resp.status_code == 400
    message = resp.data["files"][0]
    assert "MB" in message and "limit" in message
    assert memo.attachments.count() == 0


@pytest.mark.django_db
def test_a_memo_cannot_accumulate_past_its_ceiling(api, maker, memo):
    ceiling = services.MAX_MEMO_ATTACHMENTS
    per_batch = services.MAX_MEMO_ATTACHMENTS_PER_REQUEST
    while memo.attachments.count() < ceiling:
        batch = min(per_batch, ceiling - memo.attachments.count())
        assert _post(api, maker, memo,
                     [_pdf(f"f{memo.attachments.count()}-{i}.pdf")
                      for i in range(batch)]).status_code == 201
    assert memo.attachments.count() == ceiling

    resp = _post(api, maker, memo, [_pdf("one-too-many.pdf")])
    assert resp.status_code == 400
    assert str(ceiling) in resp.data["files"][0]
    assert memo.attachments.count() == ceiling


@pytest.mark.django_db
def test_the_refusal_names_the_limit_and_what_was_sent(api, maker, memo):
    resp = _post(api, maker, memo, [_pdf(f"q{i}.pdf") for i in range(15)])
    message = resp.data["files"][0]
    # Not "upload failed": the user is told the number they sent, the ceiling,
    # and what to do instead.
    assert "15" in message
    assert "smaller batches" in message
