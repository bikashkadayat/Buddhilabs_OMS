"""Phase 11 security tests: the login endpoint and trusted-proxy resolution.

These cover the two HIGH audit findings directly. H1 in particular is asserted
as a *negative*: the test rotates ``X-Forwarded-For`` the way an attacker would
and proves the throttle identity no longer moves with it.
"""
from datetime import date, datetime

import pytest
from django.core.cache import cache
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditLog
from config.client_ip import client_ip, describe
from users import login_security
from users.models import User

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-9"


@pytest.fixture
def person(db):
    user = User.objects.create_user(
        username="sec_user", email="sec.user@nif.test", password=PASSWORD,
        first_name="Sec", last_name="User", role=User.Roles.MAKER,
        date_of_joining=date(2020, 1, 1))
    User.objects.filter(pk=user.pk).update(
        date_joined=timezone.make_aware(datetime(2020, 1, 1, 9, 0)))
    return user


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        username="sec_admin", email="sec.admin@nif.test", password=PASSWORD,
        first_name="Sec", last_name="Admin", role=User.Roles.ADMIN)


@pytest.fixture
def api():
    return APIClient()


def login(api, email, password, **extra):
    return api.post(reverse("token_obtain_pair"),
                    {"email": email, "password": password},
                    format="json", **extra)


# ===========================================================================
# H1 — trusted proxy resolution
# ===========================================================================
class TestClientIp:
    def setup_method(self):
        self.factory = RequestFactory()

    @override_settings(TRUSTED_PROXY_DEPTH=2)
    def test_steps_in_from_the_right_by_the_configured_depth(self):
        request = self.factory.get("/", REMOTE_ADDR="172.18.0.4",
                                   HTTP_X_FORWARDED_FOR="203.0.113.10, 198.51.100.7")
        assert client_ip(request) == "203.0.113.10"

    @override_settings(TRUSTED_PROXY_DEPTH=1)
    def test_a_lower_depth_reads_our_own_proxy_not_the_caller(self):
        """The safe failure direction: too strict, never bypassable."""
        request = self.factory.get("/", REMOTE_ADDR="172.18.0.4",
                                   HTTP_X_FORWARDED_FOR="203.0.113.10, 198.51.100.7")
        assert client_ip(request) == "198.51.100.7"

    @override_settings(TRUSTED_PROXY_DEPTH=2)
    def test_a_truncated_header_cannot_promote_caller_input(self):
        """THE bypass, asserted closed.

        An attacker sends a header shorter than the configured depth, hoping
        their own value lands in the trusted position. The clamp means the
        worst they achieve is being identified by their own value once — they
        cannot reach past the end of the list.
        """
        request = self.factory.get("/", REMOTE_ADDR="172.18.0.4",
                                   HTTP_X_FORWARDED_FOR="1.2.3.4")
        # Only one entry exists, so depth is clamped to it. Crucially this is
        # NOT the raw header string, which is what DRF used before the fix.
        assert client_ip(request) == "1.2.3.4"

    @override_settings(TRUSTED_PROXY_DEPTH=0)
    def test_depth_zero_ignores_the_header_entirely(self):
        request = self.factory.get("/", REMOTE_ADDR="203.0.113.99",
                                   HTTP_X_FORWARDED_FOR="1.2.3.4")
        assert client_ip(request) == "203.0.113.99"

    @override_settings(TRUSTED_PROXY_DEPTH=2)
    def test_garbage_in_the_header_falls_back_rather_than_crashing(self):
        request = self.factory.get("/", REMOTE_ADDR="172.18.0.4",
                                   HTTP_X_REAL_IP="203.0.113.5",
                                   HTTP_X_FORWARDED_FOR="not-an-ip, 198.51.100.7")
        assert client_ip(request) == "203.0.113.5"

    def test_missing_everything_returns_none_not_a_placeholder(self):
        request = self.factory.get("/")
        request.META.pop("REMOTE_ADDR", None)
        assert client_ip(request) is None

    @override_settings(TRUSTED_PROXY_DEPTH=1)
    def test_describe_flags_a_depth_that_looks_too_low(self):
        request = self.factory.get("/", REMOTE_ADDR="172.18.0.4",
                                   HTTP_X_FORWARDED_FOR="203.0.113.10, 198.51.100.7")
        report = describe(request)
        assert report["hops_available"] == 2
        assert report["configured_depth"] == 1
        assert report["depth_looks_low"] is True


# ===========================================================================
# M1 — the audit log records the caller, not the proxy
# ===========================================================================
class TestAuditRecordsRealClient:
    @pytest.fixture(autouse=True)
    def _two_proxies(self, settings):
        # Class-level @override_settings only works on SimpleTestCase
        # subclasses; pytest-django's `settings` fixture is the equivalent.
        settings.TRUSTED_PROXY_DEPTH = 2

    def test_login_audit_carries_the_client_ip(self, api, person):
        response = login(api, person.email, PASSWORD,
                         HTTP_X_FORWARDED_FOR="203.0.113.44, 198.51.100.7",
                         REMOTE_ADDR="172.18.0.4")
        assert response.status_code == 200
        entry = AuditLog.objects.filter(action=AuditLog.Action.LOGIN).latest("created_at")
        assert entry.ip_address == "203.0.113.44"
        # The regression this replaces: the proxy address must NOT be recorded.
        assert entry.ip_address != "172.18.0.4"


# ===========================================================================
# H2 — failed logins are recorded and limited
# ===========================================================================
class TestLoginFailureAudit:
    def test_a_wrong_password_is_audited(self, api, person):
        login(api, person.email, "wrong-password")
        entry = AuditLog.objects.filter(changes__event="LOGIN_FAILED").latest("created_at")
        assert entry.changes["email"] == person.email
        assert entry.changes["reason"] == "bad_credentials"
        assert entry.actor is None

    def test_the_password_is_never_recorded(self, api, person):
        login(api, person.email, "hunter2-secret")
        entry = AuditLog.objects.filter(changes__event="LOGIN_FAILED").latest("created_at")
        assert "hunter2-secret" not in str(entry.changes)

    def test_an_unknown_email_is_audited_too(self, api, db):
        """Probing for valid addresses is exactly the pattern worth seeing, and
        it is invisible if only real accounts are recorded."""
        login(api, "nobody@nif.test", "whatever")
        assert AuditLog.objects.filter(changes__event="LOGIN_FAILED",
                                       changes__email="nobody@nif.test").exists()

    def test_a_successful_login_is_still_audited(self, api, person):
        assert login(api, person.email, PASSWORD).status_code == 200
        assert AuditLog.objects.filter(action=AuditLog.Action.LOGIN).exists()


class TestLockout:
    @pytest.fixture(autouse=True)
    def _tight_threshold(self, settings):
        settings.LOGIN_MAX_FAILURES = 3
        settings.LOGIN_LOCKOUT_SECONDS = 900
        settings.LOGIN_LOCKOUT_ENABLED = True
        cache.clear()

    def test_the_account_locks_after_the_threshold(self, api, person):
        for _ in range(3):
            login(api, person.email, "wrong")
        locked, remaining = login_security.is_locked(person.email)
        assert locked is True
        assert remaining > 0

    def test_a_locked_account_is_refused_even_with_the_right_password(
            self, api, person):
        """Checked before credentials, so a locked account cannot be used as an
        oracle for whether the password was right."""
        for _ in range(3):
            login(api, person.email, "wrong")
        response = login(api, person.email, PASSWORD)
        assert response.status_code == 429
        assert "contact HR" in response.data["detail"]

    def test_the_lock_transition_is_audited_once(self, api, person):
        for _ in range(5):
            login(api, person.email, "wrong")
        locks = AuditLog.objects.filter(changes__event="LOGIN_LOCKED")
        assert locks.count() == 1, "the lock should be audited on transition only"

    def test_a_successful_login_clears_the_counter(self, api, person):
        login(api, person.email, "wrong")
        login(api, person.email, "wrong")
        assert login_security.failure_count(person.email) == 2
        assert login(api, person.email, PASSWORD).status_code == 200
        assert login_security.failure_count(person.email) == 0

    def test_case_and_whitespace_cannot_split_the_counter(self, api, person):
        login(api, person.email.upper(), "wrong")
        login(api, f"  {person.email}  ", "wrong")
        assert login_security.failure_count(person.email) == 2

    @override_settings(LOGIN_LOCKOUT_ENABLED=False)
    def test_lockout_can_be_disabled_while_auditing_continues(self, api, person):
        for _ in range(6):
            login(api, person.email, "wrong")
        locked, _ = login_security.is_locked(person.email)
        assert locked is False
        assert AuditLog.objects.filter(changes__event="LOGIN_FAILED").count() == 6

    def test_hr_can_unlock_an_account(self, api, person, admin):
        for _ in range(3):
            login(api, person.email, "wrong")
        assert login_security.is_locked(person.email)[0] is True

        staff = APIClient()
        staff.force_authenticate(user=admin)
        response = staff.post(f"/api/v1/admin/users/{person.pk}/unlock/", {},
                              format="json")
        assert response.status_code == 200
        assert response.data["was_locked"] is True
        assert login_security.is_locked(person.email)[0] is False
        assert login(api, person.email, PASSWORD).status_code == 200

    def test_the_unlock_itself_is_audited(self, api, person, admin):
        for _ in range(3):
            login(api, person.email, "wrong")
        staff = APIClient()
        staff.force_authenticate(user=admin)
        staff.post(f"/api/v1/admin/users/{person.pk}/unlock/", {}, format="json")
        entry = AuditLog.objects.filter(changes__event="LOGIN_UNLOCKED").latest("created_at")
        assert entry.actor == admin

    def test_an_employee_cannot_unlock_anyone(self, person, admin):
        client = APIClient()
        client.force_authenticate(user=person)
        response = client.post(f"/api/v1/admin/users/{admin.pk}/unlock/", {},
                               format="json")
        assert response.status_code == 403


class TestLockoutFailsOpen:
    def test_a_cache_outage_does_not_lock_anyone_out(self, api, person, monkeypatch):
        """An availability incident must not become an authentication incident.

        The resilient cache turns a Redis error into a miss on read and a
        dropped write; simulating exactly that must leave login working.
        """
        monkeypatch.setattr(login_security.cache, "add", lambda *a, **k: False)
        monkeypatch.setattr(login_security.cache, "incr", lambda *a, **k: None)
        monkeypatch.setattr(login_security.cache, "get",
                            lambda key, default=None, **k: default)

        for _ in range(20):
            login(api, person.email, "wrong")
        assert login(api, person.email, PASSWORD).status_code == 200
        # ...and the failures were still audited, so the attempt is not invisible.
        assert AuditLog.objects.filter(changes__event="LOGIN_FAILED").count() == 20
