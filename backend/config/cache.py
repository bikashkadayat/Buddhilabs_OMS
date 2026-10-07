"""Cache backend that degrades instead of failing.

Wiring CACHES to Redis makes DRF throttling — which runs on EVERY request —
depend on Redis being reachable. Django's stock RedisCache raises on a
connection error, so a Redis outage would turn into a total API outage: every
endpoint 500s, the /health/ liveness probe fails, the container healthcheck
restarts the backend, and the deploy gate refuses to promote.

That is a much worse failure than the one Redis was added to solve. Before
Phase 7 the project had no CACHES block at all and ran happily on per-process
LocMemCache, so losing the cache must be a degradation, not an outage.

Failure semantics: a read behaves as a miss, a write is dropped. For throttling
that means fail-open — requests are allowed rather than rejected. That is the
right trade for an internal HR system, and it matches the pre-existing
behaviour, where throttle counters lived in per-worker memory and reset on every
restart anyway.
"""
import functools
import logging

from django.core.cache.backends.redis import RedisCache

logger = logging.getLogger(__name__)


def _degrade(default=None):
    """Run the wrapped cache operation; on a backend error, degrade quietly."""
    def decorator(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            try:
                return method(self, *args, **kwargs)
            except Exception as exc:
                # Deliberately broad: redis-py raises a family of errors and the
                # whole point is that NOTHING from the cache reaches the caller.
                logger.warning("Cache unavailable (%s: %s); degrading to no-cache",
                               type(exc).__name__, exc)
                return default() if callable(default) else default
        return wrapper
    return decorator


class ResilientRedisCache(RedisCache):
    """RedisCache that never raises at the call site."""

    def get(self, key, default=None, version=None):
        # Not decorated: a miss must return the CALLER's default, not None.
        # DRF's throttles rely on `cache.get(key, [])` yielding a list.
        try:
            return super().get(key, default, version)
        except Exception as exc:
            logger.warning("Cache unavailable (%s: %s); treating as a miss",
                           type(exc).__name__, exc)
            return default

    @_degrade(default=False)
    def set(self, *args, **kwargs):
        return super().set(*args, **kwargs)

    @_degrade(default=False)
    def add(self, *args, **kwargs):
        return super().add(*args, **kwargs)

    @_degrade(default=False)
    def touch(self, *args, **kwargs):
        return super().touch(*args, **kwargs)

    @_degrade(default=False)
    def delete(self, *args, **kwargs):
        return super().delete(*args, **kwargs)

    @_degrade(default=dict)
    def get_many(self, *args, **kwargs):
        return super().get_many(*args, **kwargs)

    @_degrade(default=list)
    def set_many(self, *args, **kwargs):
        return super().set_many(*args, **kwargs)

    @_degrade(default=None)
    def incr(self, *args, **kwargs):
        return super().incr(*args, **kwargs)

    @_degrade(default=None)
    def clear(self):
        return super().clear()
