"""Sessions: the caller's own live refresh tokens, and ending the others.

Ownership is the property under test. A session list that showed another
account's sessions would be a directory of who is signed in; an "end the
others" that accepted somebody else's token as "this one" would let a
caller decide which of a stranger's sessions survive.
"""
import pytest
from rest_framework.test import APIClient

from tenancy.tokens import TenantSafeRefreshToken
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="sess_u", email="sess_u@nif.test", password="pass12345",
        role=User.Roles.MAKER)


@pytest.fixture
def other(db):
    return User.objects.create_user(
        username="sess_o", email="sess_o@nif.test", password="pass12345",
        role=User.Roles.MAKER)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_lists_only_the_callers_own_live_sessions(user, other):
    here = TenantSafeRefreshToken.for_user(user)
    TenantSafeRefreshToken.for_user(user)
    TenantSafeRefreshToken.for_user(other)
    revoked = TenantSafeRefreshToken.for_user(user)
    revoked.blacklist()

    rows = _client(user).get("/api/v1/auth/sessions/").json()
    assert len(rows) == 2
    assert here["jti"] in {row["jti"] for row in rows}
    assert revoked["jti"] not in {row["jti"] for row in rows}


def test_end_others_keeps_this_session_and_ends_the_rest(user):
    here = TenantSafeRefreshToken.for_user(user)
    elsewhere = TenantSafeRefreshToken.for_user(user)

    response = _client(user).post("/api/v1/auth/sessions/end-others/",
                                  {"refresh": str(here)}, format="json")
    assert response.status_code == 200
    assert response.json() == {"ended": 1}

    refresh = APIClient().post("/api/v1/auth/refresh/",
                               {"refresh": str(elsewhere)}, format="json")
    assert refresh.status_code == 401, "the other session must be dead"
    rows = _client(user).get("/api/v1/auth/sessions/").json()
    assert [row["jti"] for row in rows] == [here["jti"]]


def test_another_accounts_token_cannot_be_presented_as_this_session(
        user, other):
    theirs = TenantSafeRefreshToken.for_user(other)
    TenantSafeRefreshToken.for_user(user)
    response = _client(user).post("/api/v1/auth/sessions/end-others/",
                                  {"refresh": str(theirs)}, format="json")
    assert response.status_code == 400
    assert len(_client(user).get("/api/v1/auth/sessions/").json()) == 1


def test_anonymous_callers_see_nothing():
    assert APIClient().get("/api/v1/auth/sessions/").status_code == 401
