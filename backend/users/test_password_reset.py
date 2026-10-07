"""Forgot password.

The tests that matter are the ones about what must NOT happen: the form
revealing who has an account, a reset link pointing wherever the requester's
Host header said, a link working twice or after it expired, and a reset
leaving the old sessions alive.
"""
import re
from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from tenancy.tokens import TenantSafeRefreshToken
from users.models import User

pytestmark = pytest.mark.django_db
REQUEST = "/api/v1/auth/password-reset/"
CONFIRM = "/api/v1/auth/password-reset/confirm/"


@pytest.fixture
def person(db):
    return User.objects.create_user(
        username="forgetful", email="forgetful@nif.test", password="Old-Pass-2026!",
        first_name="Asha", role=User.Roles.MAKER, must_change_password=True)


def _link():
    body = mail.outbox[-1].body
    match = re.search(r"(\S+/reset-password\?uid=([\w-]+)&token=([\w-]+))", body)
    assert match, body
    return match.group(1), match.group(2), match.group(3)


def test_a_known_address_gets_a_branded_link(person, settings):
    settings.FRONTEND_URL = "https://hr.nif.test"
    response = APIClient().post(REQUEST, {"email": "Forgetful@nif.test"}, format="json")
    assert response.status_code == 202
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["forgetful@nif.test"]
    link, _, _ = _link()
    assert link.startswith("https://hr.nif.test/reset-password?")
    assert "Powered by" in mail.outbox[0].body


def test_an_unknown_address_gets_the_same_answer_and_no_email(person):
    known = APIClient().post(REQUEST, {"email": "forgetful@nif.test"}, format="json")
    mail.outbox.clear()
    unknown = APIClient().post(REQUEST, {"email": "nobody@nif.test"}, format="json")
    assert unknown.status_code == known.status_code
    assert unknown.json() == known.json()
    assert mail.outbox == []


def test_the_link_never_follows_the_request_host(person, settings):
    """Host is user-supplied. The link is built from the canonical address."""
    settings.FRONTEND_URL = "https://hr.nif.test"
    settings.ALLOWED_HOSTS = ["*"]
    APIClient().post(REQUEST, {"email": "forgetful@nif.test"}, format="json",
                     HTTP_HOST="attacker.example")
    link, _, _ = _link()
    assert "attacker.example" not in link


def test_an_inactive_account_gets_nothing(person):
    person.is_active = False
    person.save()
    APIClient().post(REQUEST, {"email": "forgetful@nif.test"}, format="json")
    assert mail.outbox == []


def test_the_link_works_once_and_ends_every_session(person):
    TenantSafeRefreshToken.for_user(person)
    TenantSafeRefreshToken.for_user(person)
    APIClient().post(REQUEST, {"email": "forgetful@nif.test"}, format="json")
    _, uid, token = _link()

    check = APIClient().get(CONFIRM, {"uid": uid, "token": token}).json()
    assert check["valid"] is True

    done = APIClient().post(CONFIRM, {"uid": uid, "token": token,
                                      "new_password": "Brand-New-Pass-77!"}, format="json")
    assert done.status_code == 200, done.json()
    person.refresh_from_db()
    assert person.check_password("Brand-New-Pass-77!")
    assert person.must_change_password is False
    assert not OutstandingToken.objects.filter(
        user=person, blacklistedtoken__isnull=True).exists()

    again = APIClient().post(CONFIRM, {"uid": uid, "token": token,
                                       "new_password": "Another-Pass-88!"}, format="json")
    assert again.status_code == 400
    assert "expired or has already been used" in again.json()["detail"]


def test_an_expired_link_is_refused(person, settings):
    from django.contrib.auth.tokens import default_token_generator

    APIClient().post(REQUEST, {"email": "forgetful@nif.test"}, format="json")
    _, uid, token = _link()
    later = default_token_generator._now() + timedelta(
        seconds=settings.PASSWORD_RESET_TIMEOUT + 5)
    with mock.patch.object(type(default_token_generator), "_now", return_value=later):
        response = APIClient().post(CONFIRM, {"uid": uid, "token": token,
                                              "new_password": "Brand-New-Pass-77!"},
                                    format="json")
    assert response.status_code == 400
    person.refresh_from_db()
    assert person.check_password("Old-Pass-2026!")


def test_a_weak_password_is_refused_in_words(person):
    APIClient().post(REQUEST, {"email": "forgetful@nif.test"}, format="json")
    _, uid, token = _link()
    response = APIClient().post(CONFIRM, {"uid": uid, "token": token,
                                          "new_password": "123"}, format="json")
    assert response.status_code == 400
    assert response.json()["new_password"]


def test_an_operator_cannot_be_reset_from_a_customers_page(settings, django_user_model):
    from tenancy.context import no_tenant

    settings.TENANCY_PLATFORM_HOSTS = "admin.platform.test"
    with no_tenant():
        django_user_model.objects.create_user(
            username="op", email="op@platform.test", password="x-Pass-12345",
            is_platform_staff=True, organization=None)
    APIClient().post(REQUEST, {"email": "op@platform.test"}, format="json")
    assert mail.outbox == []
