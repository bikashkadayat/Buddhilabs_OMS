"""
Phase 3.5 GROUP 1 (CRITICAL) regression tests.

C1: stored-XSS in memo body must be sanitized on write.
C2: memo attachments must be gated behind authentication + CanViewMemo and
    always served as a forced download (never inline).
"""
import io

import pytest

from memos.models import Memo
from memos.sanitizers import sanitize_memo_html
from users.models import User


def _create(api, user, body):
    """
    Create a memo and put `body` into one of its content sections.

    The memo body is no longer a column on the memo: the manual's form is a list
    of titled blocks (Background, Recommendation, "+ Add more" - p.3), so the
    stored-XSS surface is MemoSection.body and that is what these tests write to.
    """
    api.force_authenticate(user)
    resp = api.post(
        "/api/v1/memos/",
        {"subject": "S", "to_line": "CEO", "memo_type": "general"},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    memo_id = resp.data["id"]

    written = api.post(
        f"/api/v1/memos/{memo_id}/sections/",
        {"sections": [{"title": "Background", "body": body}]}, format="json")
    assert written.status_code == 200, written.data
    return memo_id


def _section_body(memo_id):
    return Memo.objects.get(id=memo_id).sections.first().body


# ---------------------------------------------------------------------------
# C1: stored XSS in memo body
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_memo_body_strips_script_tag(api, maker):
    mid = _create(api, maker, '<p>hi</p><script>alert(1)</script>')
    body = _section_body(mid)
    # The executable <script> element is removed; any residual text is inert.
    assert "<script" not in body.lower()
    assert "<p>hi</p>" in body


@pytest.mark.django_db
def test_memo_body_strips_onerror_attribute(api, maker):
    mid = _create(api, maker, '<img src=x onerror="alert(1)">')
    body = _section_body(mid)
    assert "onerror" not in body.lower()
    assert "<img" not in body.lower()  # img is not on the allowlist


@pytest.mark.django_db
def test_memo_body_strips_iframe(api, maker):
    mid = _create(api, maker, '<iframe src="https://evil.test"></iframe><p>ok</p>')
    body = _section_body(mid)
    assert "<iframe" not in body.lower()
    assert "<p>ok</p>" in body


@pytest.mark.django_db
def test_memo_body_strips_javascript_uri(api, maker):
    mid = _create(api, maker, '<a href="javascript:alert(1)">x</a>')
    body = _section_body(mid)
    assert "javascript:" not in body.lower()


@pytest.mark.django_db
def test_memo_body_preserves_allowed_formatting(api, maker):
    mid = _create(
        api, maker,
        '<p><strong>bold</strong> <em>it</em></p><ul><li>a</li></ul>'
        '<h2>Title</h2><blockquote>q</blockquote>',
    )
    body = _section_body(mid)
    for token in ("<strong>", "<em>", "<ul>", "<li>", "<h2>", "<blockquote>"):
        assert token in body


@pytest.mark.django_db
def test_memo_body_link_gets_noopener_noreferrer():
    cleaned = sanitize_memo_html('<a href="https://nif.test">go</a>')
    assert 'href="https://nif.test"' in cleaned
    assert "noopener" in cleaned and "noreferrer" in cleaned


# ---------------------------------------------------------------------------
# C2: attachment access control
# ---------------------------------------------------------------------------
def _memo_with_attachment(created_by, status=Memo.Status.DRAFT_FOR_REVIEW):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from memos.services import generate_memo_number

    memo = Memo.objects.create(
        subject="S",
        memo_type=Memo.MemoType.GENERAL, status=status,
        created_by=created_by,
        memo_number=generate_memo_number(Memo.MemoType.GENERAL),
        attachment=SimpleUploadedFile("secret.pdf", b"%PDF-1.4 confidential", content_type="application/pdf"),
    )
    return memo


@pytest.mark.django_db
def test_attachment_download_requires_auth(api, maker):
    memo = _memo_with_attachment(maker)
    resp = api.get(f"/api/v1/memos/{memo.id}/attachment/")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_attachment_download_enforces_memo_permission(api, maker, other_maker):
    # An unrelated colleague is not routed on the memo and does not head its
    # department, so the attachment stays gated whatever the memo's status.
    memo = _memo_with_attachment(maker, status=Memo.Status.DRAFT)
    api.force_authenticate(other_maker)  # unrelated maker
    resp = api.get(f"/api/v1/memos/{memo.id}/attachment/")
    assert resp.status_code in (403, 404)


@pytest.mark.django_db
def test_attachment_download_allowed_for_creator(api, maker):
    memo = _memo_with_attachment(maker)
    api.force_authenticate(maker)
    resp = api.get(f"/api/v1/memos/{memo.id}/attachment/")
    assert resp.status_code == 200
    assert b"confidential" in b"".join(resp.streaming_content)


@pytest.mark.django_db
def test_attachment_download_allowed_for_workflow_assignee(api, maker, checker, approver):
    """Being routed on a memo is what grants access to its attachment."""
    from .conftest import route

    memo = _memo_with_attachment(maker, status=Memo.Status.DRAFT)
    route(api, maker, memo, (checker, "reviewer"), (approver, "approver"))

    api.force_authenticate(checker)
    assert api.get(f"/api/v1/memos/{memo.id}/attachment/").status_code == 200


@pytest.mark.django_db
def test_attachment_forces_content_disposition_attachment(api, maker):
    memo = _memo_with_attachment(maker)
    api.force_authenticate(maker)
    resp = api.get(f"/api/v1/memos/{memo.id}/attachment/")
    assert resp["Content-Disposition"].startswith("attachment;")
    assert resp["X-Content-Type-Options"] == "nosniff"


@pytest.mark.django_db
def test_attachment_never_served_inline(api, maker):
    memo = _memo_with_attachment(maker)
    api.force_authenticate(maker)
    resp = api.get(f"/api/v1/memos/{memo.id}/attachment/")
    assert "inline" not in resp["Content-Disposition"]
    assert resp["Content-Type"] == "application/octet-stream"


@pytest.mark.django_db
def test_detail_serializer_does_not_leak_media_path(api, maker):
    memo = _memo_with_attachment(maker)
    api.force_authenticate(maker)
    resp = api.get(f"/api/v1/memos/{memo.id}/")
    assert resp.status_code == 200
    # The raw "attachment" media path is never exposed; the download URL is a
    # short-lived SIGNED url (documents.protected_media), not a public /media/ path.
    assert "attachment" not in resp.data
    url = resp.data["attachment_url"]
    assert url.startswith("/api/v1/media/?") and "s=" in url and "dl=1" in url
    assert "/media/memos/" not in url  # never the raw storage path
