from rest_framework import serializers

from . import services
from .models import Attendance


class AttendanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.get_full_name", read_only=True)
    employee_id = serializers.CharField(source="employee.employee_id", read_only=True)
    department_name = serializers.CharField(source="employee.department_name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    date_bs = serializers.SerializerMethodField()
    # True/False against the CURRENT office radius, or null when the geofence is
    # off or no coordinates were captured. The stored distance stays authoritative.
    check_in_within_office = serializers.SerializerMethodField()
    check_out_within_office = serializers.SerializerMethodField()
    # Metres between the two pins -- the brief's "Distance Between Points".
    # Derived, not stored: both coordinates are already on the row, so a
    # column would be a third copy of one fact that could disagree with the
    # other two. Null until the employee has checked out.
    travel_distance_m = serializers.SerializerMethodField()
    # Google Maps deep links, built server-side so every consumer -- the
    # admin table, the modal, an export, a future mobile client -- opens the
    # same pin. NOT a stored column: a URL derived from coordinates that are
    # already stored would go stale the moment either changes.
    check_in_map_url = serializers.SerializerMethodField()
    check_out_map_url = serializers.SerializerMethodField()
    check_in_location_source_display = serializers.CharField(
        source="get_check_in_location_source_display", read_only=True)
    check_out_location_source_display = serializers.CharField(
        source="get_check_out_location_source_display", read_only=True)

    class Meta:
        model = Attendance
        fields = [
            "id", "employee", "employee_name", "employee_id", "department_name",
            "date", "date_bs", "check_in", "check_out", "status", "status_display",
            "working_hours", "remarks", "marked_by", "created_at",
            "check_in_lat", "check_in_lng", "check_in_accuracy", "check_in_address",
            "check_out_lat", "check_out_lng", "check_out_accuracy", "check_out_address",
            "check_in_distance_m", "check_out_distance_m",
            "check_in_within_office", "check_out_within_office",
            # App-based attendance (GPS provenance + derived geometry).
            "check_in_location_source", "check_out_location_source",
            "check_in_location_source_display",
            "check_out_location_source_display",
            "check_in_user_agent", "check_out_user_agent",
            "travel_distance_m", "check_in_map_url", "check_out_map_url",
            # Biometric derivation (Phase 5) — appended, so every existing
            # consumer of this payload keeps working unchanged.
            "source", "first_punch_at", "last_punch_at", "punch_count", "device",
            "browser_check_in", "browser_check_out",
            # Policy engine (Phase 8) — appended for the same reason: every
            # existing consumer of this payload keeps working unchanged.
            "regular_hours", "overtime_hours", "late_minutes",
            "comp_off_days", "comp_off_eligible", "is_wfh",
            "applied_policy", "applied_shift",
        ]
        read_only_fields = fields

    def get_date_bs(self, obj):
        from config.nepali_dates import to_bs
        return to_bs(obj.date)

    def get_travel_distance_m(self, obj):
        return services.travel_distance_m(obj)

    @staticmethod
    def _map_url(lat, lng):
        if lat is None or lng is None:
            return None
        return (f"https://www.google.com/maps/search/?api=1"
                f"&query={lat},{lng}")

    def get_check_in_map_url(self, obj):
        return self._map_url(obj.check_in_lat, obj.check_in_lng)

    def get_check_out_map_url(self, obj):
        return self._map_url(obj.check_out_lat, obj.check_out_lng)

    def get_check_in_within_office(self, obj):
        return services.is_within_office(obj.check_in_distance_m)

    def get_check_out_within_office(self, obj):
        return services.is_within_office(obj.check_out_distance_m)


class ManualAttendanceSerializer(serializers.Serializer):
    """HR/Admin manual entry or correction."""
    employee = serializers.UUIDField()
    date = serializers.DateField()
    # MANUAL_STATUSES, not Status.choices: WORK_FROM_HOME is derived from an
    # approved WFH request plus a real browser check-in, so HR must not be able
    # to type it in and bypass both gates.
    status = serializers.ChoiceField(choices=Attendance.MANUAL_STATUSES)
    check_in = serializers.DateTimeField(required=False, allow_null=True)
    check_out = serializers.DateTimeField(required=False, allow_null=True)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
