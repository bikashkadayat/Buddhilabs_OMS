"""The live feed must not be pinned to the top by a terminal's clock glitch.

The main-gate device holds seven punches stamped 2033-02-22 — a real firmware
clock fault, and real rows that are never deleted because the raw punch log is
the audit trail every derived figure traces back to. But the feed is ordered by
timestamp descending, so those seven sat permanently at the top of it and pushed
the whole of today off the end of a twenty-row list. The dashboard looked stale
on a system that was collecting perfectly.
"""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from biometric.models import AttendancePunch, BiometricDevice, BiometricEmployee

pytestmark = pytest.mark.django_db


@pytest.fixture
def device(db):
    return BiometricDevice.objects.create(name="Main Gate", label="main-gate",
                                          host="192.168.77.201")


@pytest.fixture
def punch_for(device, hr):
    """A punch attributed to the HR user, who can see the whole org's feed."""
    BiometricEmployee.objects.create(device=device, device_user_id="7", user=hr,
                                     device_name="Hari")

    def _make(when, **overrides):
        data = dict(device=device, employee_device_id="7", user=hr,
                    employee_name="Hari", timestamp=when, punch=0,
                    source=AttendancePunch.Source.HISTORY)
        data.update(overrides)
        return AttendancePunch.objects.create(**data)
    return _make


def _feed(auth, user):
    response = auth(user).get(reverse("attendance-dashboard"))
    assert response.status_code == 200
    return response.data["recent_punches"]


def test_a_glitched_future_punch_is_not_shown(auth, hr, punch_for):
    punch_for(timezone.now() + timedelta(days=2400))   # the 2033 case
    assert _feed(auth, hr) == []


def test_it_does_not_hide_the_real_punches_behind_it(auth, hr, punch_for):
    """The actual regression: today's punch must still be reachable in the feed."""
    punch_for(timezone.now() + timedelta(days=2400))
    today = punch_for(timezone.now() - timedelta(minutes=5))

    feed = _feed(auth, hr)
    assert [p["punch_id"] for p in feed] == [str(today.pk)]


def test_a_punch_slightly_ahead_of_the_server_is_still_shown(auth, hr, punch_for):
    """A terminal running a little fast is normal — this device is ~100s off.

    Its punches are genuine and must not be filtered out, which is why the
    tolerance is not zero.
    """
    ahead = punch_for(timezone.now() + timedelta(minutes=2))
    assert [p["punch_id"] for p in _feed(auth, hr)] == [str(ahead.pk)]


def test_the_glitched_rows_are_filtered_not_deleted(auth, hr, punch_for):
    """Filtered at the view, never removed. The punch log is append-only audit
    data, and a correction belongs on the derived attendance row instead."""
    future = punch_for(timezone.now() + timedelta(days=2400))
    _feed(auth, hr)
    assert AttendancePunch.objects.filter(pk=future.pk).exists()
