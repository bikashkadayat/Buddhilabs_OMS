"""App-based attendance: GPS check-in/out, provenance, and the tenant's mode.

WHAT THIS PHASE ACTUALLY WAS, because it changes what is worth testing.

The backend has supported app check-in since Phase 4: the columns, the
coordinate validation, the geofence, the reverse geocoding and the audit
trail were all live. What had happened is that the FRONTEND was switched off
-- the mutation, the buttons and the location capture were commented out in
`AttendanceWidget.jsx` and `AttendanceRecords.jsx` with "re-enable by
restoring" notes.

So the tests below concentrate on the three things that are genuinely new,
plus the one defect the work uncovered:

  1. `location_source` -- whether a fix came from satellites or an IP lookup
  2. `attendance_mode` -- per tenant, and enforced at both ends
  3. the distance between the two pins
  4. **every attendance setting was resolved deployment-wide**, so each
     tenant's geofence was measured against one office. See
     `TestConfigurationIsPerTenant`.
"""
from decimal import Decimal

import pytest
from django.test import override_settings

from attendance import config, services
from attendance.models import Attendance

pytestmark = pytest.mark.django_db

# Kathmandu Durbar Square and Thamel, ~1.3 km apart.
OFFICE_LAT, OFFICE_LNG = "27.7045", "85.3070"
THAMEL_LAT, THAMEL_LNG = 27.7154, 85.3123

no_network = override_settings(ATTENDANCE_REVERSE_GEOCODE=False)


@pytest.fixture
def api_employee(employee):
    """An authenticated client and the employee it is signed in as."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(employee)
    return client, employee


@pytest.fixture
def nif(db):
    """The organization these tests run inside.

    With TENANCY_ENABLED off (the default in this suite) `active_organization`
    still resolves to the single organization, which is what makes the
    per-tenant settings readable here at all.
    """
    from tenancy.models import Organization

    return Organization.objects.get(slug="nif")


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """The per-tenant settings row is cached. Each test gets a clean read."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# 1. GPS capture and provenance
# ---------------------------------------------------------------------------
class TestLocationProvenance:
    def test_a_client_reported_source_is_kept(self):
        parsed = services.parse_coordinates({
            "latitude": THAMEL_LAT, "longitude": THAMEL_LNG,
            "accuracy": 12, "location_source": "gps"})
        assert parsed["source"] == Attendance.LocationSource.GPS

    @pytest.mark.parametrize("hostile", [
        "satellite", "GPS'); DROP TABLE--", "<script>", "", "   ",
        "gps extra", "IP_LOOKUP",
    ])
    def test_anything_outside_the_vocabulary_becomes_unknown(self, hostile):
        """This is a client-supplied string about to be stored and shown to
        HR as provenance. An arbitrary value would be an unvalidated label
        on evidence."""
        parsed = services.parse_coordinates({
            "latitude": THAMEL_LAT, "longitude": THAMEL_LNG,
            "location_source": hostile})
        assert parsed["source"] == Attendance.LocationSource.UNKNOWN

    @pytest.mark.parametrize("accuracy,expected", [
        (5, Attendance.LocationSource.GPS),
        (80, Attendance.LocationSource.GPS),
        (100, Attendance.LocationSource.GPS),
        (350, Attendance.LocationSource.NETWORK),
        (1999, Attendance.LocationSource.NETWORK),
        (2000, Attendance.LocationSource.NETWORK),
        (5000, Attendance.LocationSource.IP),
        (40000, Attendance.LocationSource.IP),
    ])
    def test_accuracy_bands_infer_a_source_when_the_client_is_silent(
            self, accuracy, expected):
        assert services.classify_accuracy(accuracy) == expected

    def test_no_accuracy_means_unknown_rather_than_a_guess(self):
        """Inventing `gps` would put false confidence on a pin somebody may
        later be disciplined over."""
        assert services.classify_accuracy(None) == \
            Attendance.LocationSource.UNKNOWN
        assert services.classify_accuracy("nonsense") == \
            Attendance.LocationSource.UNKNOWN

    @no_network
    def test_check_in_stores_the_source_and_the_device(self, api_employee):
        client, employee = api_employee
        response = client.post("/api/v1/attendance/check-in/", {
            "latitude": THAMEL_LAT, "longitude": THAMEL_LNG,
            "accuracy": 9, "location_source": "gps"}, format="json",
            HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14) Chrome/120")
        assert response.status_code == 201, response.data
        record = Attendance.objects.get(employee=employee)
        assert record.check_in_location_source == "gps"
        assert "Android" in record.check_in_user_agent

    @no_network
    def test_a_silent_client_still_gets_a_source(self, api_employee):
        client, employee = api_employee
        client.post("/api/v1/attendance/check-in/", {
            "latitude": THAMEL_LAT, "longitude": THAMEL_LNG,
            "accuracy": 3000}, format="json")
        record = Attendance.objects.get(employee=employee)
        assert record.check_in_location_source == Attendance.LocationSource.IP

    @no_network
    def test_the_user_agent_is_truncated_not_rejected(self, api_employee):
        client, employee = api_employee
        client.post("/api/v1/attendance/check-in/", {
            "latitude": THAMEL_LAT, "longitude": THAMEL_LNG},
            format="json", HTTP_USER_AGENT="U" * 900)
        record = Attendance.objects.get(employee=employee)
        assert len(record.check_in_user_agent) == 256


# ---------------------------------------------------------------------------
# 2. coordinates cannot be invented
# ---------------------------------------------------------------------------
class TestCoordinateValidation:
    @pytest.mark.parametrize("lat,lng", [
        (91, 85.3), (-91, 85.3), (27.7, 181), (27.7, -181),
        ("abc", "def"), (None, 85.3), ("", ""), (float("inf"), 85.3),
    ])
    def test_an_impossible_coordinate_is_dropped(self, lat, lng):
        assert services.parse_coordinates(
            {"latitude": lat, "longitude": lng}) == {}

    @no_network
    def test_a_rejected_coordinate_blocks_the_check_in_when_location_is_required(
            self, api_employee):
        """Not "accepted without a location" -- the gate and the validation
        have to agree, or a client sending latitude 91 checks in with no pin
        at all and nobody notices."""
        client, _ = api_employee
        response = client.post("/api/v1/attendance/check-in/",
                               {"latitude": 91, "longitude": 85.3},
                               format="json")
        assert response.status_code == 400
        assert "Location is required" in response.data["detail"]
        assert not Attendance.objects.exists()

    @no_network
    @override_settings(ATTENDANCE_REQUIRE_LOCATION=False)
    def test_but_a_tenant_may_allow_location_less_check_in(self,
                                                            api_employee):
        client, employee = api_employee
        response = client.post("/api/v1/attendance/check-in/", {},
                               format="json")
        assert response.status_code == 201
        record = Attendance.objects.get(employee=employee)
        assert record.check_in is not None
        assert record.check_in_lat is None


# ---------------------------------------------------------------------------
# 3. distance between the two pins
# ---------------------------------------------------------------------------
class TestTravelDistance:
    def test_null_until_both_ends_exist(self, employee):
        record = Attendance.objects.create(
            employee=employee, date=services.now_local().date(),
            check_in_lat=Decimal("27.704500"),
            check_in_lng=Decimal("85.307000"))
        assert services.travel_distance_m(record) is None

    def test_measured_once_both_are_there(self, employee):
        record = Attendance.objects.create(
            employee=employee, date=services.now_local().date(),
            check_in_lat=Decimal("27.704500"),
            check_in_lng=Decimal("85.307000"),
            check_out_lat=Decimal("27.715400"),
            check_out_lng=Decimal("85.312300"))
        distance = services.travel_distance_m(record)
        assert 1300 <= distance <= 1340

    def test_zero_when_they_did_not_move(self, employee):
        record = Attendance.objects.create(
            employee=employee, date=services.now_local().date(),
            check_in_lat=Decimal("27.704500"),
            check_in_lng=Decimal("85.307000"),
            check_out_lat=Decimal("27.704500"),
            check_out_lng=Decimal("85.307000"))
        assert services.travel_distance_m(record) == 0.0

    @no_network
    def test_it_reaches_the_api(self, api_employee):
        client, _ = api_employee
        client.post("/api/v1/attendance/check-in/", {
            "latitude": 27.7045, "longitude": 85.3070}, format="json")
        response = client.post("/api/v1/attendance/check-out/", {
            "latitude": THAMEL_LAT, "longitude": THAMEL_LNG}, format="json")
        assert response.status_code == 200
        assert 1300 <= response.data["travel_distance_m"] <= 1340

    @no_network
    def test_and_a_google_maps_link_for_each_pin(self, api_employee):
        client, _ = api_employee
        response = client.post("/api/v1/attendance/check-in/", {
            "latitude": 27.7045, "longitude": 85.3070}, format="json")
        url = response.data["check_in_map_url"]
        assert url.startswith("https://www.google.com/maps/search/?api=1")
        assert "27.704500,85.307000" in url
        # Null rather than a broken link before the employee checks out.
        assert response.data["check_out_map_url"] is None


# ---------------------------------------------------------------------------
# 4. attendance mode, per tenant
# ---------------------------------------------------------------------------
class TestAttendanceMode:
    def test_the_default_is_both(self):
        """The only safe default for an existing deployment: anything
        narrower switches off a method customers are already using, the
        morning this ships."""
        assert config.attendance_mode() == config.MODE_BOTH
        assert config.app_check_in_allowed() is True
        assert config.biometric_allowed() is True

    def test_an_unknown_value_falls_back_rather_than_locking_everyone_out(
            self, settings):
        settings.ATTENDANCE_MODE = "typo_mode"
        assert config.attendance_mode() == config.MODE_BOTH

    @pytest.mark.parametrize("mode,app,bio", [
        (config.MODE_BOTH, True, True),
        (config.MODE_APP_ONLY, True, False),
        (config.MODE_BIOMETRIC_ONLY, False, True),
    ])
    def test_each_mode_permits_what_it_says(self, settings, mode, app, bio):
        settings.ATTENDANCE_MODE = mode
        assert config.app_check_in_allowed() is app
        assert config.biometric_allowed() is bio

    @no_network
    def test_biometric_only_refuses_an_app_check_in(self, api_employee,
                                                     settings):
        settings.ATTENDANCE_MODE = config.MODE_BIOMETRIC_ONLY
        client, _ = api_employee
        response = client.post("/api/v1/attendance/check-in/", {
            "latitude": THAMEL_LAT, "longitude": THAMEL_LNG}, format="json")
        assert response.status_code == 403
        assert "biometric devices only" in response.data["detail"]
        assert not Attendance.objects.exists()

    @no_network
    def test_and_hides_the_button_rather_than_only_refusing_the_press(
            self, api_employee, settings):
        settings.ATTENDANCE_MODE = config.MODE_BIOMETRIC_ONLY
        client, _ = api_employee
        today = client.get("/api/v1/attendance/today/")
        assert today.data["can_check_in"] is False
        assert today.data["app_check_in_allowed"] is False
        assert today.data["attendance_mode"] == config.MODE_BIOMETRIC_ONLY

    @no_network
    def test_check_out_is_NOT_gated_by_the_mode(self, api_employee,
                                                 settings):
        """If an administrator switches to biometric-only at lunchtime, an
        employee who checked in through the app this morning must still be
        able to check out -- otherwise the setting change strands an open
        record and the day reads as if they never went home."""
        client, _ = api_employee
        assert client.post("/api/v1/attendance/check-in/", {
            "latitude": THAMEL_LAT, "longitude": THAMEL_LNG},
            format="json").status_code == 201

        settings.ATTENDANCE_MODE = config.MODE_BIOMETRIC_ONLY
        from django.core.cache import cache

        cache.clear()
        response = client.post("/api/v1/attendance/check-out/", {
            "latitude": THAMEL_LAT, "longitude": THAMEL_LNG}, format="json")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 5. THE DEFECT: configuration was deployment-wide, not per tenant
# ---------------------------------------------------------------------------
class TestConfigurationIsPerTenant:
    """`OrganizationSettings` has carried `office_lat`, `office_lng`,
    `office_radius_m`, `max_accuracy_m`, `require_location`, `office_name`,
    `office_start`, `absent_cutoff`, `full_day_hours`, `half_day_hours` and
    `tracking_start` since Phase S6 -- editable from the console, exported in
    the tenant's bundle, and read by NOTHING.

    For the geofence that was worse than being unset: every customer was
    judged "at office" against one configured point, so a tenant that has
    never been near it was permanently away from an office that is not
    theirs -- and app-based attendance is sold on that judgement.
    """

    def test_a_tenant_office_overrides_the_deployment(self, settings, nif):
        from tenancy.models import OrganizationSettings

        settings.ATTENDANCE_OFFICE_LAT = OFFICE_LAT
        settings.ATTENDANCE_OFFICE_LNG = OFFICE_LNG
        settings.ATTENDANCE_OFFICE_RADIUS_M = 150
        assert services.office_location() == (27.7045, 85.3070, 150.0)

        OrganizationSettings.objects.update_or_create(
            organization=nif,
            defaults={"office_lat": Decimal("27.715400"),
                      "office_lng": Decimal("85.312300"),
                      "office_radius_m": 300})
        from django.core.cache import cache

        cache.clear()
        assert services.office_location() == (27.7154, 85.3123, 300.0)

    def test_the_deployment_is_still_the_fallback(self, settings, nif):
        """Which is what makes this safe for the single-tenant deployment:
        a tenant that has configured nothing behaves exactly as before."""
        from tenancy.models import OrganizationSettings

        settings.ATTENDANCE_OFFICE_LAT = OFFICE_LAT
        settings.ATTENDANCE_OFFICE_LNG = OFFICE_LNG
        OrganizationSettings.objects.update_or_create(
            organization=nif, defaults={"office_lat": None,
                                        "office_lng": None})
        from django.core.cache import cache

        cache.clear()
        assert services.office_location()[:2] == (27.7045, 85.3070)

    def test_require_location_is_per_tenant(self, settings, nif):
        from tenancy.models import OrganizationSettings

        settings.ATTENDANCE_REQUIRE_LOCATION = True
        assert services.location_required() is True

        OrganizationSettings.objects.update_or_create(
            organization=nif, defaults={"require_location": False})
        from django.core.cache import cache

        cache.clear()
        assert services.location_required() is False

    def test_false_is_a_real_answer_not_an_absent_one(self, settings, nif):
        """The trap in a nullable boolean: treating `False` as "not set"
        would silently re-impose the platform default on every tenant that
        had deliberately turned it off."""
        from tenancy.models import OrganizationSettings

        settings.ATTENDANCE_REQUIRE_LOCATION = True
        OrganizationSettings.objects.update_or_create(
            organization=nif, defaults={"require_location": False})
        from django.core.cache import cache

        cache.clear()
        assert services.location_required() is False

    def test_max_accuracy_is_per_tenant(self, settings, nif):
        from tenancy.models import OrganizationSettings

        settings.ATTENDANCE_MAX_ACCURACY_M = 100
        assert services.max_accuracy_m() == 100.0

        OrganizationSettings.objects.update_or_create(
            organization=nif, defaults={"max_accuracy_m": 25})
        from django.core.cache import cache

        cache.clear()
        assert services.max_accuracy_m() == 25.0

    def test_the_mode_is_per_tenant(self, nif):
        from tenancy.models import OrganizationSettings

        OrganizationSettings.objects.update_or_create(
            organization=nif,
            defaults={"attendance_mode": "biometric_only"})
        from django.core.cache import cache

        cache.clear()
        assert config.attendance_mode() == config.MODE_BIOMETRIC_ONLY
        assert config.app_check_in_allowed() is False

    def test_a_console_change_takes_effect_without_waiting_for_the_cache(
            self, nif):
        """The row is cached for five minutes. Without eviction on save, an
        operator changing a customer's office would appear not to have
        worked for long enough to change it again."""
        from tenancy.models import OrganizationSettings

        row, _ = OrganizationSettings.objects.update_or_create(
            organization=nif, defaults={"max_accuracy_m": 25})
        assert services.max_accuracy_m() == 25.0

        row.max_accuracy_m = 400
        row.save()                      # post_save evicts the cache
        assert services.max_accuracy_m() == 400.0

    def test_attendance_still_works_with_no_tenant_in_context(self,
                                                               settings):
        """Reached from management commands and the biometric ingest, which
        have no request. Must fall back, not raise."""
        settings.ATTENDANCE_REQUIRE_LOCATION = True
        from tenancy.context import no_tenant

        with no_tenant():
            assert services.location_required() is True
            assert config.attendance_mode() == config.MODE_BOTH
