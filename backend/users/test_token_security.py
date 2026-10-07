"""M3: refresh-token rotation, blacklist-on-rotation, and logout."""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from users.models import User


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="tok", email="tok@nif.test", password="pass12345",
        role=User.Roles.MAKER, department="Finance",
    )


@pytest.mark.django_db
def test_refresh_rotates_token(api, user):
    refresh = str(RefreshToken.for_user(user))
    resp = api.post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")
    assert resp.status_code == 200
    # Rotation issues a NEW refresh token distinct from the one presented.
    assert "refresh" in resp.data
    assert resp.data["refresh"] != refresh


@pytest.mark.django_db
def test_rotated_old_token_is_rejected(api, user):
    refresh = str(RefreshToken.for_user(user))
    api.post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")  # rotates + blacklists old
    # The old refresh token can no longer be used.
    again = api.post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")
    assert again.status_code == 401


@pytest.mark.django_db
def test_logout_blacklists_refresh(api, user):
    refresh = str(RefreshToken.for_user(user))
    api.force_authenticate(user)
    resp = api.post("/api/v1/auth/logout/", {"refresh": refresh}, format="json")
    assert resp.status_code == 205
    # After logout the refresh token is dead.
    api.force_authenticate(None)
    used = api.post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")
    assert used.status_code == 401


@pytest.mark.django_db
def test_logout_requires_auth(api, user):
    refresh = str(RefreshToken.for_user(user))
    assert api.post("/api/v1/auth/logout/", {"refresh": refresh}, format="json").status_code == 401


@pytest.mark.django_db
def test_access_token_lifetime_is_short():
    from django.conf import settings
    assert settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds() <= 30 * 60
