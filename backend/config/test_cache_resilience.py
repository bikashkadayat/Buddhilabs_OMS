"""A cache outage must degrade the app, not take it down.

Wiring CACHES to Redis (Phase 7) put a network dependency in front of DRF
throttling, which runs on every request. Without the resilient backend, a Redis
blip would 500 every endpoint, fail the /health/ liveness probe, restart the
container and block deploys — trading "no live dashboard" for "no application".
"""
import pytest
from django.core.cache import caches
from rest_framework.test import APIClient

from config.cache import ResilientRedisCache

HEALTH = "/api/v1/health/"


class UnreachableCache(ResilientRedisCache):
    """Stands in for a Redis that is not answering."""

    def __init__(self, *args, **kwargs):
        super().__init__("redis://127.0.0.1:1/0", {})


@pytest.fixture
def broken_cache(settings):
    settings.CACHES = {"default": {
        "BACKEND": "config.test_cache_resilience.UnreachableCache", "LOCATION": ""}}
    caches._settings = settings.CACHES
    try:
        del caches._connections.default
    except AttributeError:
        pass
    yield caches["default"]
    try:
        del caches._connections.default
    except AttributeError:
        pass


def test_reads_degrade_to_a_miss(broken_cache):
    assert broken_cache.get("anything") is None
    assert broken_cache.get("anything", "fallback") == "fallback"
    assert broken_cache.get_many(["a", "b"]) == {}


def test_writes_are_dropped_silently(broken_cache):
    assert broken_cache.set("k", "v") is False
    assert broken_cache.add("k", "v") is False
    assert broken_cache.delete("k") is False
    assert broken_cache.touch("k") is False
    assert broken_cache.incr("k") is None


@pytest.mark.django_db
def test_the_liveness_probe_survives_a_cache_outage(broken_cache):
    """This is the one that matters: /health/ drives the container healthcheck
    and the deploy gate."""
    response = APIClient().get(HEALTH)
    assert response.status_code == 200
    assert response.data["database"] == "up"


@pytest.mark.django_db
def test_throttled_endpoints_still_answer_during_a_cache_outage(broken_cache):
    """Throttling fails OPEN. Requests are allowed rather than rejected, which
    matches the pre-Phase-7 behaviour where counters lived in per-worker memory
    and reset on every restart."""
    response = APIClient().get("/api/v1/leaves/")
    assert response.status_code == 401, "auth still enforced; only the cache degraded"


@pytest.mark.django_db
def test_health_is_not_throttled():
    """Even with a working cache, the probe must not be rate-limited — the
    healthcheck polls it every 30s from every container."""
    from config.health_views import HealthView

    assert HealthView.throttle_classes == []
