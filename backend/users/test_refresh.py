"""Phase 2 (folded from Phase 1): token refresh returns 401, not 500, for a
refresh token whose user was deleted or deactivated."""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from users.models import User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="refresh_u", email="refresh_u@nif.test", password="pass12345",
        role=User.Roles.MAKER,
    )


@pytest.mark.django_db
def test_refresh_with_valid_token_still_works(user):
    token = str(RefreshToken.for_user(user))
    resp = APIClient().post("/api/v1/auth/refresh/", {"refresh": token}, format="json")
    assert resp.status_code == 200
    assert "access" in resp.data


@pytest.mark.django_db
def test_refresh_with_deleted_user_returns_401(user):
    token = str(RefreshToken.for_user(user))
    user.delete()
    resp = APIClient().post("/api/v1/auth/refresh/", {"refresh": token}, format="json")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_refresh_with_inactive_user_returns_401(user):
    token = str(RefreshToken.for_user(user))
    user.is_active = False
    user.save(update_fields=["is_active"])
    resp = APIClient().post("/api/v1/auth/refresh/", {"refresh": token}, format="json")
    assert resp.status_code == 401
