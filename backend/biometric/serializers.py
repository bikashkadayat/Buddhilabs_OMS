import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from users.models import User

from .models import AttendancePunch, BiometricDevice, BiometricEmployee, DeviceSyncLog

DEFAULT_MAX_BATCH = 500

# Trailing "Z", "+05:45" or "+0545" — the marker that a timestamp is unambiguous.
_UTC_OFFSET_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)


class MappedUserSerializer(serializers.ModelSerializer):
    """Compact employee block reused by every mapping payload."""
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    department_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "employee_id", "full_name", "email", "designation",
                  "department_name", "is_active"]
        read_only_fields = fields


class BiometricEmployeeSerializer(serializers.ModelSerializer):
    user_detail = MappedUserSerializer(source="user", read_only=True)
    device_label = serializers.CharField(source="device.label", read_only=True)
    device_display_name = serializers.CharField(source="device.name", read_only=True)
    is_mapped = serializers.BooleanField(read_only=True)
    is_unbounded = serializers.BooleanField(read_only=True)
    punch_count = serializers.SerializerMethodField()

    class Meta:
        model = BiometricEmployee
        fields = [
            "id", "device", "device_label", "device_display_name", "device_user_id",
            "user", "user_detail", "device_name", "privilege", "card", "group_id",
            "is_active", "is_mapped", "is_unbounded", "punch_count",
            "effective_from", "effective_until", "superseded_by",
            "first_seen_at", "last_synced_at", "mapped_at", "mapped_by",
            "created_at", "updated_at",
        ]
        # effective_from/effective_until stay writable: HR has to be able to
        # correct a validity window, and those two dates are what bound every
        # backfill. superseded_by is not — only remap may set it.
        read_only_fields = [
            "id", "is_mapped", "is_unbounded", "punch_count", "superseded_by",
            "first_seen_at", "last_synced_at", "mapped_at", "mapped_by",
            "created_at", "updated_at",
        ]

    def update(self, instance, validated_data):
        """``device`` and ``device_user_id`` are immutable after creation.

        The device is the source of truth for identity, so the OMS stores its
        identifiers and never rewrites them. They stay writable on CREATE
        because HR legitimately pre-registers a mapping before the first roster
        sync; they are dropped here on UPDATE.

        Silently dropping rather than raising: a PATCH that resends the whole
        object (which is what the UI does) would otherwise fail on unchanged
        values. A genuine attempt to CHANGE them still cannot succeed — the
        model's save() raises, which is the real guarantee. This just keeps the
        common case from erroring on a no-op.
        """
        validated_data.pop("device_user_id", None)
        validated_data.pop("device", None)
        return super().update(instance, validated_data)

    def get_punch_count(self, obj):
        return AttendancePunch.objects.filter(
            device_id=obj.device_id, employee_device_id=obj.device_user_id,
        ).count()

    def validate(self, attrs):
        start = attrs.get("effective_from", getattr(self.instance, "effective_from", None))
        end = attrs.get("effective_until", getattr(self.instance, "effective_until", None))
        if start and end and start > end:
            raise serializers.ValidationError(
                {"effective_from": "Start date cannot be after the end date."})
        return attrs


class MappingWriteSerializer(serializers.Serializer):
    """Explicit map/remap payload — keeps the reassignment path off PATCH."""
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    effective_from = serializers.DateField(required=False, allow_null=True)


class BackfillRequestSerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False, allow_null=True)
    date_to = serializers.DateField(required=False, allow_null=True)
    # Deliberately not defaulted to True anywhere: an unbounded backfill has to
    # be a conscious act, because it is the operation that can steal a departed
    # employee's attendance history.
    allow_unbounded = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if attrs.get("date_from") and attrs.get("date_to") and attrs["date_from"] > attrs["date_to"]:
            raise serializers.ValidationError({"date_from": "Start date cannot be after the end date."})
        return attrs


class UnmapRequestSerializer(serializers.Serializer):
    detach_punches = serializers.BooleanField(required=False, default=True)


class SuggestionSerializer(serializers.Serializer):
    user = MappedUserSerializer(read_only=True)
    score = serializers.FloatField(read_only=True)
    match_type = serializers.CharField(read_only=True)
    reasons = serializers.ListField(child=serializers.CharField(), read_only=True)


class UnmappedGroupSerializer(serializers.Serializer):
    """One actionable row in the unmapped queue: a device ID, not a punch.

    HR resolves an unknown *device ID*, so grouping is the unit of work — a flat
    punch list would show the same problem hundreds of times.
    """
    device = serializers.UUIDField(read_only=True)
    device_label = serializers.CharField(read_only=True)
    device_user_id = serializers.CharField(read_only=True)
    device_name = serializers.CharField(read_only=True, allow_blank=True)
    mapping_id = serializers.UUIDField(read_only=True, allow_null=True)
    punch_count = serializers.IntegerField(read_only=True)
    first_punch_date = serializers.DateField(read_only=True, allow_null=True)
    last_punch_date = serializers.DateField(read_only=True, allow_null=True)
    suggestions = SuggestionSerializer(many=True, read_only=True)


class AttendancePunchSerializer(serializers.ModelSerializer):
    employee_name_oms = serializers.CharField(source="user.get_full_name", read_only=True, default=None)
    device_label = serializers.CharField(source="device.label", read_only=True)

    class Meta:
        model = AttendancePunch
        fields = ["id", "device", "device_label", "employee_device_id", "employee_name",
                  "user", "employee_name_oms", "timestamp", "local_date", "punch",
                  "punch_label", "verify_status", "source", "is_processed", "received_at"]
        read_only_fields = fields


# ===========================================================================
# Ingest (Phase 6) — collector -> Django
# ===========================================================================

class AwareDateTimeField(serializers.DateTimeField):
    """A DateTimeField that refuses naive input.

    DRF does NOT hand a naive datetime through: with USE_TZ=True it silently
    localises it into settings.TIME_ZONE, so ``timezone.is_naive()`` on the
    parsed value is always False and would never catch anything. The only place
    the ambiguity is still visible is the raw string, so that is what we check.

    This matters because the collector converts device-local time to an explicit
    offset before sending. A payload arriving without one means that conversion
    did not happen — the timestamps are ambiguous, and silently stamping them
    with the server's zone would quietly misfile every punch from a terminal in
    any other timezone.
    """

    def to_internal_value(self, value):
        parsed = super().to_internal_value(value)
        if isinstance(value, str) and not _UTC_OFFSET_RE.search(value.strip()):
            raise serializers.ValidationError(
                "Timestamp must include a UTC offset (e.g. 2026-08-03T09:15:04+05:45). "
                "Naive timestamps are ambiguous — set the sending client's device "
                "timezone. (`device_sync` does not use this path: it reads the "
                "terminal directly and applies BiometricDevice.device_timezone.)"
            )
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed


class PunchPayloadSerializer(serializers.Serializer):
    """One punch as the collector sends it."""
    employee_id = serializers.CharField(max_length=64)
    name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    timestamp = AwareDateTimeField()
    punch = serializers.IntegerField(min_value=0, max_value=255)
    status = serializers.IntegerField(min_value=0, max_value=255, required=False, default=0)

    def validate_timestamp(self, value):
        if value > timezone.now() + timedelta(days=1):
            raise serializers.ValidationError(
                "Timestamp is more than a day in the future — check the device clock.")
        return value

    def validate_employee_id(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("employee_id cannot be blank.")
        return cleaned


class SinglePunchSerializer(PunchPayloadSerializer):
    """POST /punch/ — a live punch, sent one at a time."""
    source = serializers.ChoiceField(
        choices=AttendancePunch.Source.choices, required=False,
        default=AttendancePunch.Source.LIVE)
    queue_depth = serializers.IntegerField(min_value=0, required=False, default=0)


class BulkSyncSerializer(serializers.Serializer):
    """POST /bulk-sync/ — a backlog chunk."""
    punches = serializers.ListField(child=PunchPayloadSerializer(), allow_empty=True)
    source = serializers.ChoiceField(
        choices=AttendancePunch.Source.choices, required=False,
        default=AttendancePunch.Source.HISTORY)
    queue_depth = serializers.IntegerField(min_value=0, required=False, default=0)

    def validate_punches(self, value):
        limit = getattr(settings, "BIOMETRIC_MAX_BATCH", DEFAULT_MAX_BATCH)
        if len(value) > limit:
            raise serializers.ValidationError(
                f"Batch too large: {len(value)} punches, limit is {limit}. "
                f"Lower the sending client's batch size, or raise "
                f"BIOMETRIC_MAX_BATCH.")
        return value


class RosterEmployeeSerializer(serializers.Serializer):
    employee_id = serializers.CharField(max_length=64)
    name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    privilege = serializers.IntegerField(min_value=0, required=False, default=0)
    card = serializers.IntegerField(required=False, default=0)
    group_id = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")

    def validate_employee_id(self, value):
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("employee_id cannot be blank.")
        return cleaned


class RosterSyncSerializer(serializers.Serializer):
    """POST /roster-sync/ — the device's enrolled users."""
    employees = serializers.ListField(child=RosterEmployeeSerializer(), allow_empty=True)


# ===========================================================================
# Organization Settings -> Attendance -> Biometric Devices
# ===========================================================================

class BiometricDeviceSerializer(serializers.ModelSerializer):
    """A device as an organization administrator sees and edits it.

    ``comm_key`` is write-only: it is the terminal's PIN, and nothing the
    browser does needs it back. ``has_comm_key`` says whether one is set.
    ``label`` is optional on create -- the form asks for a name, and the
    short handle the management commands use is derived from it.
    """
    device_type_display = serializers.CharField(source="get_device_type_display", read_only=True)
    connection_status_display = serializers.CharField(
        source="get_connection_status_display", read_only=True)
    sync_interval_display = serializers.CharField(
        source="get_sync_interval_minutes_display", read_only=True)
    comm_key = serializers.IntegerField(write_only=True, required=False,
                                        min_value=0, max_value=999999)
    has_comm_key = serializers.SerializerMethodField()
    label = serializers.CharField(max_length=100, required=False, allow_blank=True)
    sync_mode = serializers.SerializerMethodField()
    next_sync_due_at = serializers.SerializerMethodField()
    # Annotated by the viewset; absent (None) when the serializer is used on
    # a bare instance, e.g. straight after create.
    attendance_imported = serializers.IntegerField(read_only=True, default=None)
    mapped_users = serializers.IntegerField(read_only=True, default=None)
    unmapped_users = serializers.IntegerField(read_only=True, default=None)

    class Meta:
        model = BiometricDevice
        fields = [
            "id", "name", "label", "device_type", "device_type_display",
            "host", "port", "location", "is_active", "device_timezone",
            "sync_interval_minutes", "sync_interval_display", "sync_mode",
            "comm_key", "has_comm_key",
            "connection_status", "connection_status_display",
            "last_seen_at", "last_sync_at", "last_sync_attempt_at",
            "last_sync_status", "last_sync_error", "last_sync_imported",
            "next_sync_due_at", "clock_drift_seconds",
            "device_info", "device_info_at", "hardware_serial", "serial_number",
            "attendance_imported", "mapped_users", "unmapped_users",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "connection_status", "last_seen_at", "last_sync_at", "last_sync_attempt_at",
            "last_sync_status", "last_sync_error", "last_sync_imported",
            "clock_drift_seconds", "device_info", "device_info_at", "hardware_serial",
            "serial_number", "created_at", "updated_at",
        ]
        # The model-level (organization, name) constraints are checked in
        # validate(), where the organization is known, with a readable message.
        validators = []

    def get_has_comm_key(self, obj):
        return bool(obj.comm_key)

    def get_sync_mode(self, obj):
        # A device with no address cannot be pulled: it pushes (ADMS or an
        # on-site collector posting to the ingest API).
        return "pull" if obj.host else "push"

    def get_next_sync_due_at(self, obj):
        if not (obj.is_active and obj.host and obj.sync_interval_minutes):
            return None
        if obj.last_sync_attempt_at is None:
            return timezone.now().isoformat()
        return (obj.last_sync_attempt_at
                + timedelta(minutes=obj.sync_interval_minutes)).isoformat()

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Enter a name for the device.")
        return value

    def validate_device_timezone(self, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise serializers.ValidationError(f"{value!r} is not a known timezone.") from exc
        return value

    def validate(self, attrs):
        from django.utils.text import slugify

        from .devices import DeviceAddressError, check_device_address

        instance = self.instance
        host = attrs.get("host", instance.host if instance else "")
        port = attrs.get("port", instance.port if instance else 4370)
        if "host" in attrs or "port" in attrs:
            if host:
                try:
                    check_device_address(host, port)
                except DeviceAddressError as exc:
                    raise serializers.ValidationError({"host": str(exc)}) from exc
            attrs["host"] = (host or "").strip()

        existing = BiometricDevice.objects.all()
        if instance is not None:
            existing = existing.exclude(pk=instance.pk)
        name = attrs.get("name")
        if name and existing.filter(name__iexact=name).exists():
            raise serializers.ValidationError(
                {"name": "Another device in your organization already has this name."})

        label = (attrs.get("label") or "").strip()
        if instance is None and not label:
            base = slugify(attrs.get("name", "")) or "device"
            label, n = base[:90], 2
            while existing.filter(label=label).exists():
                label = f"{base[:90]}-{n}"
                n += 1
        if label:
            if existing.filter(label=label).exists():
                raise serializers.ValidationError(
                    {"label": "Another device in your organization already uses this label."})
            attrs["label"] = label
        elif "label" in attrs:
            attrs.pop("label")
        return attrs


class DeviceConnectionTestSerializer(serializers.Serializer):
    """"Test before saving": the address fields of the Add Device form."""
    host = serializers.CharField(max_length=255)
    port = serializers.IntegerField(default=4370, min_value=1, max_value=65535)
    comm_key = serializers.IntegerField(default=0, min_value=0, max_value=999999)
    device_type = serializers.ChoiceField(
        choices=BiometricDevice.DeviceType.choices, default=BiometricDevice.DeviceType.ZKTECO)


class DeviceSyncLogSerializer(serializers.ModelSerializer):
    sync_type_display = serializers.CharField(source="get_sync_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    trigger_display = serializers.CharField(source="get_trigger_display", read_only=True)
    triggered_by_name = serializers.SerializerMethodField()
    duration_seconds = serializers.FloatField(read_only=True)

    class Meta:
        model = DeviceSyncLog
        fields = ["id", "sync_type", "sync_type_display", "status", "status_display",
                  "trigger", "trigger_display", "triggered_by_name",
                  "started_at", "finished_at", "duration_seconds",
                  "records_received", "records_created", "records_duplicate",
                  "records_unmapped", "records_invalid", "error"]
        read_only_fields = fields

    def get_triggered_by_name(self, obj):
        return obj.triggered_by.get_full_name() if obj.triggered_by_id else None


class AutoMatchRequestSerializer(serializers.Serializer):
    device = serializers.UUIDField(required=False, allow_null=True)
    apply = serializers.BooleanField(default=False)
