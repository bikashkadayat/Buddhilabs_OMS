"""Blocker 1 — uploaded media must never be reachable without a valid signature."""
import time

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from rest_framework.test import APIClient

from documents.protected_media import signed_media_url, _sign


@pytest.fixture
def stored_file(db):
    name = "memos/attachments/test/secret.pdf"
    if default_storage.exists(name):
        default_storage.delete(name)
    default_storage.save(name, ContentFile(b"%PDF-1.4 confidential"))
    yield name
    if default_storage.exists(name):
        default_storage.delete(name)


def _c():
    c = APIClient()
    c.defaults["SERVER_NAME"] = "localhost"
    return c


@pytest.mark.django_db
def test_raw_media_route_is_gone(stored_file):
    # The old unauthenticated catch-all must not exist any more.
    r = _c().get(f"/media/{stored_file}")
    assert r.status_code == 404, r.status_code


@pytest.mark.django_db
def test_valid_signed_url_streams_file(stored_file):
    url = signed_media_url(stored_file, download=True)
    assert url.startswith("/api/v1/media/?")
    r = _c().get(url)
    assert r.status_code == 200
    assert b"confidential" in b"".join(r.streaming_content)
    assert r["X-Content-Type-Options"] == "nosniff"


@pytest.mark.django_db
def test_missing_or_tampered_signature_denied(stored_file):
    assert _c().get(f"/api/v1/media/?p={stored_file}").status_code == 403          # no sig
    exp = int(time.time()) + 600
    assert _c().get(f"/api/v1/media/?p={stored_file}&e={exp}&s=deadbeef").status_code == 403


@pytest.mark.django_db
def test_expired_signature_denied(stored_file):
    exp = int(time.time()) - 5
    sig = _sign(stored_file, exp)
    assert _c().get(f"/api/v1/media/?p={stored_file}&e={exp}&s={sig}").status_code == 403


@pytest.mark.django_db
def test_path_traversal_denied(db):
    name = "../config/settings.py"
    exp = int(time.time()) + 600
    sig = _sign(name, exp)
    assert _c().get(f"/api/v1/media/?p={name}&e={exp}&s={sig}").status_code == 403


def test_signed_url_none_for_empty():
    assert signed_media_url(None) is None
    assert signed_media_url("") is None


@pytest.mark.django_db
def test_profile_photo_serializer_returns_signed_url():
    from users.models import User
    from users.serializers import UserSerializer
    u = User.objects.create_user(username="pht", email="pht@nif.test", password="pass12345", role=User.Roles.MAKER)
    u.profile_photo.save("profiles/test/av.png", ContentFile(b"png"), save=True)
    try:
        val = UserSerializer(u).data["profile_photo"]
        assert val.startswith("/api/v1/media/?"), val
        assert "/media/profiles" not in val  # never the raw path
    finally:
        u.profile_photo.delete(save=False)
