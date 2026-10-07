"""Brute-force protection for the login endpoint.

Phase 11 audit findings H1 + H2. Before this, ``/api/v1/auth/login/`` was
protected only by DRF's ``anon: 20/min`` throttle -- which was itself bypassable
(H1) -- and a wrong password was recorded nowhere at all (H2). Together that
meant an attacker could guess passwords at full speed and leave no trace, so a
breach could not even be dated afterwards.

Three layers, deliberately in this order:

1. **Throttle by IP** -- a coarse ceiling on how fast anyone can try. Now that
   ``NUM_PROXIES`` is set, the identity behind it is trustworthy.
2. **Lockout by account** -- narrow and targeted. Rate limiting by IP alone does
   not stop a slow distributed attack against one known email address.
3. **Audit every failure** -- so the first two can be reasoned about after the
   fact, and so an attack in progress is visible rather than inferred.

Counters live in the cache, which is deliberate. The cache is
``ResilientRedisCache``: on a Redis outage reads miss and writes drop, so
lockout **fails open** and a cache failure locks nobody out of their own system.
That is the right trade for an internal HR tool -- the throttle and the audit
trail still apply, and an availability incident must not become an authentication
incident.
"""
import logging

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.throttling import SimpleRateThrottle

from config.client_ip import client_ip

logger = logging.getLogger(__name__)

# Tuned for a ~40-person organisation. A real user who has forgotten their
# password tries three or four times and asks HR; ten failures in a quarter of
# an hour is not that person.
#
# Read through functions rather than captured at import: module-level constants
# would freeze whatever the settings were when the process booted, which makes
# them untestable with @override_settings and unchangeable without a redeploy.
DEFAULT_MAX_FAILURES = 10
DEFAULT_FAILURE_WINDOW_SECONDS = 900
DEFAULT_LOCKOUT_SECONDS = 900


def max_failures():
    return int(getattr(settings, "LOGIN_MAX_FAILURES", DEFAULT_MAX_FAILURES))


def failure_window_seconds():
    return int(getattr(settings, "LOGIN_FAILURE_WINDOW",
                       DEFAULT_FAILURE_WINDOW_SECONDS))


def lockout_seconds():
    return int(getattr(settings, "LOGIN_LOCKOUT_SECONDS", DEFAULT_LOCKOUT_SECONDS))


def lockout_enabled():
    """Lockout can be disabled entirely, leaving throttle + audit in place.

    Offered because "lock the account" and "tell me about it" are different
    risk appetites: an organisation that would rather never lock out its own
    director can run with alerting only.
    """
    return bool(getattr(settings, "LOGIN_LOCKOUT_ENABLED", True))

# Alert thresholds -- read by the Phase 11 alerting rules.
ACCOUNT_ALERT_FAILURES = 20
SYSTEM_ALERT_FAILURES = 200

def _normalise(email):
    """Lower-cased email, so ``Ram@nif.org`` and ``ram@nif.org`` share a counter."""
    return (email or "").strip().lower()


# ---------------------------------------------------------------------------
# cache keys -- PER TENANT (Phase S3)
# ---------------------------------------------------------------------------
#
# These were the bare strings "login:fail:<email>" and "login:lock:<email>".
# Phase S2 made an email address unique only WITHIN an organization, so the
# same address is now two different accounts at two different companies -- and
# a single keyspace made them share one counter. Two consequences, both real:
#
#   * somebody failing to log in at Company A locked out a DIFFERENT person at
#     Company B who happened to use the same address. A free, remote, targeted
#     denial of service against any account whose email you can guess.
#   * the failure count in Company A's audit log included Company B's attempts.
#
# The tenant is passed in by the caller rather than read from context, because
# login runs BEFORE a user is known and the tenant comes from the host -- which
# the login view has already resolved.
def _fail_key(email, organization=None):
    from tenancy.keys import tenant_key

    return tenant_key("login", "fail", _normalise(email),
                      organization=organization)


def _lock_key(email, organization=None):
    from tenancy.keys import tenant_key

    return tenant_key("login", "lock", _normalise(email),
                      organization=organization)


# ---------------------------------------------------------------------------
# throttling
# ---------------------------------------------------------------------------
class LoginRateThrottle(SimpleRateThrottle):
    """Per-IP ceiling on login attempts.

    Scoped separately from ``anon`` so the login endpoint can be far stricter
    than, say, the health probe, without making every anonymous endpoint
    unusable.
    """
    scope = "login"

    def get_ident(self, request):
        # Uses the same trusted-proxy resolution as the audit log, so a throttle
        # decision and its audit record can never disagree about the caller.
        return client_ip(request) or "unknown"

    def get_cache_key(self, request, view):
        """Per IP, PER TENANT (Phase S3).

        The bucket is still keyed on the client address -- that is the point of
        a login throttle -- but it is namespaced by the tenant being logged
        into. Without that, an office NAT shared by two customers gave them one
        combined 10/min budget, so one company's staff arriving at 9am could
        throttle the other company's out of its own system.
        """
        from tenancy.keys import current_token

        ident = self.get_ident(request)
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{current_token()}:{ident}",
        }


# ---------------------------------------------------------------------------
# account lockout
# ---------------------------------------------------------------------------
def is_locked(email, organization=None):
    """Whether this account is currently locked, and for how much longer."""
    key = _lock_key(email, organization)
    until = cache.get(key)
    if not until:
        return False, 0
    remaining = int(until - timezone.now().timestamp())
    if remaining <= 0:
        cache.delete(key)
        return False, 0
    return True, remaining


def record_failure(email, organization=None):
    """Count a failed attempt; lock the account once the threshold is crossed.

    Returns ``(failures, locked_now)``. ``locked_now`` is True only on the
    transition, so the caller can audit the lock once rather than on every
    subsequent attempt.
    """
    normalised = _normalise(email)
    if not normalised:
        return 0, False

    key = _fail_key(normalised, organization)
    # add() then incr(): add is a no-op when the key exists, which is what gives
    # the window a fixed start rather than one that slides forward with every
    # attempt and never expires.
    cache.add(key, 0, failure_window_seconds())
    failures = cache.incr(key, 1)
    if failures is None:  # cache degraded -- fail open, deliberately
        return 0, False

    if failures >= max_failures() and lockout_enabled():
        locked_already, _ = is_locked(normalised, organization)
        cache.set(_lock_key(normalised, organization),
                  timezone.now().timestamp() + lockout_seconds(),
                  lockout_seconds())
        return failures, not locked_already
    return failures, False


def clear_failures(email, organization=None):
    """Reset on a successful sign-in, so an unlucky morning does not accumulate
    across a whole day."""
    normalised = _normalise(email)
    cache.delete(_fail_key(normalised, organization))
    cache.delete(_lock_key(normalised, organization))


def unlock(email, organization=None):
    """Explicit HR/Admin unlock -- the escape hatch for the person locked out
    ten minutes before a meeting."""
    clear_failures(email, organization)


def failure_count(email, organization=None):
    return cache.get(_fail_key(email, organization)) or 0


# ---------------------------------------------------------------------------
# audit payloads
# ---------------------------------------------------------------------------
def failure_changes(email, request, failures, reason="bad_credentials"):
    """What gets written into ``AuditLog.changes`` for a failed attempt.

    The attempted email is recorded because without it the entry answers
    nothing; the password never is, not even a hash of it.
    """
    return {
        "event": "LOGIN_FAILED",
        "email": _normalise(email)[:254],
        "reason": reason,
        "failures_in_window": failures,
        "client_ip": client_ip(request),
        "window_seconds": failure_window_seconds(),
    }


def lockout_changes(email, request, failures):
    return {
        "event": "LOGIN_LOCKED",
        "email": _normalise(email)[:254],
        "failures_in_window": failures,
        "client_ip": client_ip(request),
        "lockout_seconds": lockout_seconds(),
    }
