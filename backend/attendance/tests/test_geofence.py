"""Office geofence — distance capture at check-in/out.

The geofence answers "was the employee actually at the office?" with a measured
number instead of trusting a reverse-geocoded street name, which is the part of
a location that can be wrong by a whole neighbourhood.
"""
from decimal import Decimal

import pytest
from django.test import override_settings

from attendance import services
from attendance.models import Attendance

pytestmark = pytest.mark.django_db

# Kathmandu Durbar Square (the "office" in these tests) and Thamel, ~1.3 km apart.
OFFICE_LAT, OFFICE_LNG = "27.7045", "85.3070"
THAMEL_LAT, THAMEL_LNG = 27.7154, 85.3123

office_configured = override_settings(
    ATTENDANCE_OFFICE_LAT=OFFICE_LAT,
    ATTENDANCE_OFFICE_LNG=OFFICE_LNG,
    ATTENDANCE_OFFICE_RADIUS_M=150,
    ATTENDANCE_OFFICE_NAME="NIF Office",
    ATTENDANCE_REVERSE_GEOCODE=False,  # never hit the network from a test
)


# --------------------------------------------------------------------------- #
# distance maths
# --------------------------------------------------------------------------- #
def test_haversine_matches_known_distance():
    d = services.haversine_m(27.7045, 85.3070, THAMEL_LAT, THAMEL_LNG)
    assert 1300 <= d <= 1340  # ~1.32 km on the ground

    assert services.haversine_m(27.7045, 85.3070, 27.7045, 85.3070) == 0


@office_configured
def test_distance_accepts_the_decimals_the_view_passes():
    """parse_coordinates yields Decimals; they must flow through unconverted."""
    coords = services.parse_coordinates({"latitude": "27.704550", "longitude": "85.307050"})
    assert isinstance(coords["lat"], Decimal)

    d = services.distance_from_office(coords["lat"], coords["lng"])
    assert d < 20
    assert services.is_within_office(d) is True


@office_configured
def test_a_fix_outside_the_radius_is_flagged():
    d = services.distance_from_office(THAMEL_LAT, THAMEL_LNG)
    assert d > 1000
    assert services.is_within_office(d) is False


# --------------------------------------------------------------------------- #
# the feature is opt-in — nothing is measured or judged without coordinates
# --------------------------------------------------------------------------- #
@override_settings(ATTENDANCE_OFFICE_LAT="", ATTENDANCE_OFFICE_LNG="")
def test_geofence_off_by_default():
    assert services.office_location() is None
    assert services.distance_from_office(THAMEL_LAT, THAMEL_LNG) is None
    assert services.is_within_office(500) is None


@override_settings(ATTENDANCE_OFFICE_LAT="not-a-number", ATTENDANCE_OFFICE_LNG="85.3")
def test_malformed_office_config_disables_rather_than_crashes():
    assert services.office_location() is None
    assert services.distance_from_office(THAMEL_LAT, THAMEL_LNG) is None


@office_configured
def test_missing_coordinates_are_not_judged():
    """A check-in with no captured location must not read as "away from office"."""
    assert services.distance_from_office(None, None) is None
    assert services.is_within_office(None) is None


# --------------------------------------------------------------------------- #
# end to end through the API
# --------------------------------------------------------------------------- #
@office_configured
def test_check_in_stores_the_distance(auth, maker):
    resp = auth(maker).post(
        "/api/v1/attendance/check-in/",
        {"latitude": "27.704550", "longitude": "85.307050", "accuracy": 12.5},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    assert resp.data["check_in_distance_m"] < 20
    assert resp.data["check_in_within_office"] is True

    rec = Attendance.objects.get(employee=maker)
    assert rec.check_in_distance_m is not None


@office_configured
@override_settings(ATTENDANCE_REQUIRE_LOCATION=False)
def test_check_in_without_coordinates_leaves_distance_null(auth, maker):
    # Optional-capture mode (the fallback for HTTP / location-less devices):
    # a check-in with no coordinates still succeeds and simply stores no distance.
    resp = auth(maker).post("/api/v1/attendance/check-in/", {}, format="json")

    assert resp.status_code == 201, resp.data
    assert resp.data["check_in_distance_m"] is None
    assert resp.data["check_in_within_office"] is None


# --------------------------------------------------------------------------- #
# location gate — ATTENDANCE_REQUIRE_LOCATION (default on)
# --------------------------------------------------------------------------- #
@override_settings(ATTENDANCE_REQUIRE_LOCATION=True, ATTENDANCE_REVERSE_GEOCODE=False)
def test_check_in_without_location_is_refused_when_required(auth, maker):
    resp = auth(maker).post("/api/v1/attendance/check-in/", {}, format="json")
    assert resp.status_code == 400
    assert "location is required" in str(resp.data).lower()
    # Nothing must be recorded for a refused attempt.
    assert not Attendance.objects.filter(employee=maker).exists()


@override_settings(ATTENDANCE_REQUIRE_LOCATION=True, ATTENDANCE_REVERSE_GEOCODE=False)
def test_check_in_with_location_succeeds_when_required(auth, maker):
    resp = auth(maker).post(
        "/api/v1/attendance/check-in/",
        {"latitude": "27.7045", "longitude": "85.3070", "accuracy": 12.5},
        format="json",
    )
    assert resp.status_code == 201, resp.data
    assert Attendance.objects.get(employee=maker).check_in_lat is not None


@override_settings(ATTENDANCE_REQUIRE_LOCATION=True, ATTENDANCE_REVERSE_GEOCODE=False)
def test_check_out_without_location_is_refused_when_required(auth, maker):
    auth(maker).post(
        "/api/v1/attendance/check-in/",
        {"latitude": "27.7045", "longitude": "85.3070"}, format="json",
    )
    resp = auth(maker).post("/api/v1/attendance/check-out/", {}, format="json")
    assert resp.status_code == 400
    assert "location is required" in str(resp.data).lower()
    # The refused check-out must not have stamped a time.
    assert Attendance.objects.get(employee=maker).check_out is None


@office_configured
def test_a_widened_radius_reclassifies_a_stored_distance(auth, maker):
    """Distance is stored; the verdict is derived — so moving the radius
    re-judges history without rewriting what was actually measured."""
    auth(maker).post(
        "/api/v1/attendance/check-in/",
        {"latitude": THAMEL_LAT, "longitude": THAMEL_LNG},
        format="json",
    )
    rec = Attendance.objects.get(employee=maker)
    measured = rec.check_in_distance_m

    assert services.is_within_office(measured) is False
    with override_settings(ATTENDANCE_OFFICE_RADIUS_M=2000):
        assert services.is_within_office(measured) is True

    rec.refresh_from_db()
    assert rec.check_in_distance_m == measured  # the measurement never changed
