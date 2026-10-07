"""Rate limiting on the unauthenticated iClock/PUSH path.

The module docstring in biometric/push_views.py lists throttling among the
controls that compensate for this endpoint having no authentication, but for a
long time nothing implemented it: these are plain ``django.views.View``
subclasses, so DRF throttling never runs on them, and ``DeviceRateThrottle``
keys on ``request.auth`` being a BiometricDevice — only ever true on the
HMAC-signed REST ingest path.

Two limits, with different jobs:

  * per-SERIAL, generous — a terminal flushing a backlog posts in bursts, and
    throttling real attendance away is a worse failure than the flood it guards
    against;
  * per-SOURCE on unregistered serials, tighter — that is the enumeration path.
    A caller walking the serial space presents a NEW serial every request, so a
    per-serial counter would never see it; only a per-source one does.

Refusals are 429, which is non-2xx, so the terminal keeps its records and
retries. That is the acknowledgement contract this module is built around: OK
means "you may forget it", and a throttled request must never say OK.
"""
import pytest

from biometric import push_views
from biometric.models import BiometricDevice

pytestmark = pytest.mark.django_db

SERIAL = "CJXK205060099"


@pytest.fixture
def push_device(db):
    return BiometricDevice.objects.create(
        name="Main Gate", label="main-gate", host="192.168.77.201",
        serial_number=SERIAL, device_timezone="Asia/Kathmandu", is_active=True)


@pytest.fixture
def low_limits(monkeypatch):
    """Shrink the windows so the test is about behaviour, not endurance."""
    monkeypatch.setattr(push_views, "PUSH_RATE_PER_MINUTE", 5)
    monkeypatch.setattr(push_views, "PUSH_UNKNOWN_RATE_PER_MINUTE", 3)


def _ping(client, serial=SERIAL):
    return client.get(f"/iclock/ping?SN={serial}")


def test_a_registered_device_is_throttled_past_its_limit(client, push_device, low_limits):
    for i in range(5):
        assert _ping(client).status_code != 429, f"throttled early, on request {i + 1}"

    throttled = _ping(client)
    assert throttled.status_code == 429
    assert throttled["Retry-After"] == "60"


def test_a_throttled_request_never_says_OK(client, push_device, low_limits):
    """The device must keep its records — OK would make it erase them."""
    for _ in range(5):
        _ping(client)

    throttled = _ping(client)
    assert throttled.status_code == 429
    assert b"OK" not in throttled.content


def test_unregistered_serials_are_throttled_per_source(client, low_limits):
    """Enumeration guard: a different serial every time, so only a per-source
    counter can register the pattern."""
    for i in range(3):
        resp = _ping(client, serial=f"UNKNOWN-SERIAL-{i}")
        # 401 = "not a registered device", the correct refusal before the limit.
        assert resp.status_code == 401, f"expected 401 on attempt {i + 1}, got {resp.status_code}"

    blocked = _ping(client, serial="UNKNOWN-SERIAL-99")
    assert blocked.status_code == 429


def test_the_limit_does_not_leak_between_serials(client, push_device, low_limits):
    """A noisy unknown serial must not starve the real terminal."""
    for i in range(3):
        _ping(client, serial=f"NOISY-{i}")

    assert _ping(client).status_code != 429, "registered device throttled by another serial's traffic"
