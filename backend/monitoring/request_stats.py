"""A rolling count of responses, so "5xx rate" is a number and not a feeling.

PHASE S11 PART 3. The Phase S10 load test produced 339 identical HTTP 500s
and the only reason anybody noticed is that the load driver was counting
status codes on purpose. Nothing in the platform was.

WHY THE CACHE AND NOT A TABLE. This has to work on the one path where the
database is least trustworthy: a request that is already failing. With
`ATOMIC_REQUESTS` on, a 500 means the transaction is being rolled back, so a
row written during it would vanish -- and a row written after it would need
its own connection at the exact moment connections may be the problem. A
counter in the cache costs no transaction and cannot make a bad request
worse.

The cost is honest and bounded: the numbers live in the cache, so they reset
when it does, and with a per-process cache each worker counts only its own
traffic. That is one more reason the launch gate insists on Redis
(`tenancy.L002`); with Redis the buckets are shared and the rate is the
platform's, not one worker's.

BUCKETED BY MINUTE, not a single running pair, so the window genuinely
slides: a spike an hour ago must not still be inflating the rate now.
"""
import logging
import time

from django.core.cache import cache

logger = logging.getLogger(__name__)

WINDOW_MINUTES = 5
PREFIX = "monitoring:requests"
# Long enough to outlive the window with room for clock skew, short enough
# that abandoned buckets evict themselves.
BUCKET_TTL = (WINDOW_MINUTES + 2) * 60


def _bucket(when=None):
    return int((when or time.time()) // 60)


def record(status_code, when=None):
    """Count one response. Never raises -- it is on every request path."""
    try:
        bucket = _bucket(when)
        _increment(f"{PREFIX}:total:{bucket}")
        if status_code >= 500:
            _increment(f"{PREFIX}:error:{bucket}")
    except Exception:                                   # noqa: BLE001
        logger.debug("request stats not recorded", exc_info=True)


def _increment(key):
    """`incr` where the backend supports it, falling back to get/set.

    `cache.incr` raises ValueError when the key is absent, which is the
    common case for the first request in a minute -- so the add-then-incr
    dance is not defensive clutter, it is the normal path.
    """
    try:
        cache.incr(key)
    except ValueError:
        # The key is absent, which is the normal path for the first request
        # in a minute. `add` sets it only if it is STILL absent and reports
        # which happened -- so a race where another thread created it first
        # falls through to `incr` instead of silently losing this request.
        if not cache.add(key, 1, BUCKET_TTL):
            try:
                cache.incr(key)
            except ValueError:
                pass


def window(minutes=WINDOW_MINUTES, when=None):
    """``(total, errors, rate_percent)`` over the last ``minutes``."""
    now = _bucket(when)
    total = errors = 0
    for offset in range(minutes):
        bucket = now - offset
        total += int(cache.get(f"{PREFIX}:total:{bucket}") or 0)
        errors += int(cache.get(f"{PREFIX}:error:{bucket}") or 0)
    rate = (100.0 * errors / total) if total else 0.0
    return total, errors, rate


def reset():
    """Drop every bucket in the window. For tests and for an operator who
    has just fixed the cause and wants the alert to clear now."""
    now = _bucket()
    for offset in range(-2, WINDOW_MINUTES + 2):
        cache.delete(f"{PREFIX}:total:{now - offset}")
        cache.delete(f"{PREFIX}:error:{now - offset}")


class ResponseStatsMiddleware:
    """Count every response. Deliberately the outermost thing that counts.

    Placed FIRST in MIDDLEWARE so it sees the status code that actually
    reaches the client -- including the 400 Django raises for a disallowed
    host and the 500 produced by a view that blew up, both of which happen
    outside the inner middleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        record(getattr(response, "status_code", 0))
        return response
