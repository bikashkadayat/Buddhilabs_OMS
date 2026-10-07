"""
Phase MEMO-P1-PRODUCTION-BLOCKERS: the media endpoint has its own throttle.

With no authentication classes, DRF treated every attachment fetch as
anonymous and applied `anon` -- 20/min KEYED BY IP -- so one ten-attachment
memo spent half an office's shared budget and downloads failed with 429.
"""
from rest_framework.throttling import ScopedRateThrottle

from documents.protected_media import ProtectedMediaView


def test_media_has_its_own_scope_not_the_anonymous_bucket():
    assert ProtectedMediaView.throttle_classes == [ScopedRateThrottle]
    assert ProtectedMediaView.throttle_scope == "media"


def test_the_media_rate_clears_a_multi_attachment_memo(settings):
    """
    A memo may hold 20 attachments and a reader may preview AND download each,
    so the ceiling has to clear that comfortably or the bug simply returns at a
    higher number.
    """
    from memos.services import MAX_MEMO_ATTACHMENTS
    rate = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["media"]
    count, period = rate.split("/")
    assert period.startswith("min")
    assert int(count) >= MAX_MEMO_ATTACHMENTS * 2


def test_media_is_cacheable_only_while_its_signature_lives(db, settings):
    """
    Phase OMS-SIDEBAR-AVATAR-SYNC.

    The response carried no cache directive at all, so browsers fell back to
    heuristic caching and an avatar could be re-fetched on any remount —
    spending the media throttle for no benefit. The fix must not overshoot in
    the other direction: caching past the signature would leave a readable copy
    after the link that authorised it expired, so max-age is the REMAINING life
    of the URL rather than a fixed number.
    """
    import time
    from django.core.files.uploadedfile import SimpleUploadedFile
    from rest_framework.test import APIClient

    from users.models import User
    from documents.protected_media import signed_media_url

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c636000000200010005fe02fea7b1a4b40000000049454e44ae426082"
    )
    user = User.objects.create_user(
        username="cacheuser", email="cache@nif.test", password="pass12345",
        first_name="Cache", last_name="User", role="maker",
    )
    user.profile_photo.save("c.png", SimpleUploadedFile("c.png", png), save=True)

    url = signed_media_url(user.profile_photo.name, ttl=300, user=user)
    resp = APIClient().get(url)
    assert resp.status_code == 200

    cache = resp.headers.get("Cache-Control", "")
    assert "private" in cache, f"a user-bound file must not be cached by proxies: {cache!r}"
    age = int(cache.split("max-age=")[1].split(",")[0])
    assert 0 < age <= 300, f"max-age must not outlive the 300s signature: {age}"
