"""
Phase OMS-AVATAR-FINAL-FIX: the login response must sign the photo URL.

The bug: this endpoint returned `user.profile_photo.url` — the raw /media/
path. The project deliberately does not serve that (config.urls removed the
public catch-all; files come only through documents.protected_media), so every
login handed the client a URL guaranteed to 404 and the avatar fell back to
initials. Every OTHER place this field is serialised already signed it, which
is why the header and the sidebar disagreed depending on which response each
had last seen.
"""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from users.models import User


@pytest.fixture
def api():
    # Local, because the shared `api` fixture lives in leaves/tests/conftest.py
    # and this app has no conftest of its own.
    return APIClient()

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c636000000200010005fe02fea7b1a4b40000000049454e44ae426082"
)


@pytest.fixture
def person(db):
    user = User.objects.create_user(
        username="photo.user", email="photo.user@nif.test", password="pass12345",
        first_name="Photo", last_name="User", role="maker",
    )
    user.profile_photo.save("me.png", SimpleUploadedFile("me.png", PNG), save=True)
    return user


def _login(api, user):
    return api.post("/api/v1/auth/login/",
                    {"email": user.email, "password": "pass12345"}, format="json")


@pytest.mark.django_db
def test_login_returns_a_signed_photo_url(api, person):
    resp = _login(api, person)
    assert resp.status_code == 200, resp.data
    url = resp.data["user"]["profile_photo"]

    assert url, "the login response dropped the photo entirely"
    # The exact failure: a raw path the server will not serve.
    assert not url.startswith("/media/"), (
        f"login returned an unserved raw media path: {url}")
    assert url.startswith("/api/v1/media/"), url
    assert "s=" in url and "e=" in url, f"URL is not signed or has no expiry: {url}"


@pytest.mark.django_db
def test_the_signed_url_actually_resolves(api, person):
    """A signature is only worth having if the endpoint accepts it."""
    url = _login(api, person).data["user"]["profile_photo"]
    fetched = api.get(url)
    assert fetched.status_code == 200, (
        f"the URL handed out at login does not resolve: {fetched.status_code}")


@pytest.mark.django_db
def test_login_and_current_user_agree(api, person):
    """
    The two endpoints the client reads identity from must not disagree about
    where the photo lives — that disagreement is what put a photo in the header
    and initials in the sidebar at the same moment.
    """
    login_url = _login(api, person).data["user"]["profile_photo"]
    api.force_authenticate(person)
    me_url = api.get("/api/v1/auth/user/").data["profile_photo"]

    strip = lambda u: u.split("&e=")[0]        # noqa: E731 — expiry differs per call
    assert strip(login_url) == strip(me_url)


@pytest.mark.django_db
def test_no_photo_is_still_null(api, db):
    plain = User.objects.create_user(
        username="nophoto", email="nophoto@nif.test", password="pass12345",
        first_name="No", last_name="Photo", role="maker",
    )
    assert _login(api, plain).data["user"]["profile_photo"] is None
