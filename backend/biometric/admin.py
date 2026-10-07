from django.contrib import admin, messages
from django.utils.html import format_html

from .matching import suggest_for_mapping
from .models import AttendancePunch, BiometricDevice, BiometricEmployee, DeviceSyncLog
from .services import rotate_device_api_key, unmap_employee


@admin.register(BiometricDevice)
class BiometricDeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "label", "host", "is_active", "connection_status",
                    "key_state", "last_seen_at", "last_sync_at")
    list_filter = ("is_active", "connection_status")
    search_fields = ("name", "label", "host", "location")
    readonly_fields = ("api_key_prefix", "api_key_hash", "api_key_set_at", "last_seen_at",
                       "last_sync_at", "last_punch_at", "clock_drift_seconds",
                       "connection_status", "created_at", "updated_at", "created_by")
    actions = ("issue_api_key",)
    fieldsets = (
        (None, {"fields": ("name", "label", "is_active", "location")}),
        ("Connection", {"fields": ("host", "port", "device_timezone")}),
        ("Credentials", {
            "fields": ("api_key_prefix", "api_key_set_at"),
            "description": "Keys are stored hashed. Use the “Issue a new API key” action to "
                           "generate one — it is displayed once and cannot be recovered afterwards.",
        }),
        ("Health", {"fields": ("connection_status", "last_seen_at", "last_sync_at",
                               "last_punch_at", "clock_drift_seconds")}),
        ("Audit", {"fields": ("created_by", "created_at", "updated_at")}),
    )

    @admin.display(description="API key")
    def key_state(self, obj):
        if obj.has_api_key:
            return format_html("<span style='color:#15803d'>set</span> ({})", obj.api_key_prefix)
        return format_html("<span style='color:#b91c1c'>not set</span>")

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Issue a new API key (invalidates the current one)")
    def issue_api_key(self, request, queryset):
        for device in queryset:
            raw_key = rotate_device_api_key(device, actor=request.user)
            # Shown once, in the response only — never logged, never stored.
            self.message_user(
                request,
                format_html(
                    "New key for <b>{}</b> — copy it now, it will not be shown again:<br>"
                    "<code style='user-select:all'>{}</code>", device.name, raw_key,
                ),
                level=messages.WARNING,
            )


class UnmappedFilter(admin.SimpleListFilter):
    """The unmapped queue as a one-click admin filter."""
    title = "mapping state"
    parameter_name = "mapping_state"

    def lookups(self, request, model_admin):
        return (("unmapped", "Unmapped (needs HR action)"), ("mapped", "Mapped"),
                ("retired", "Retired / superseded"))

    def queryset(self, request, queryset):
        if self.value() == "unmapped":
            return queryset.filter(user__isnull=True, is_active=True)
        if self.value() == "mapped":
            return queryset.filter(user__isnull=False, is_active=True)
        if self.value() == "retired":
            return queryset.filter(is_active=False)
        return queryset


@admin.register(BiometricEmployee)
class BiometricEmployeeAdmin(admin.ModelAdmin):
    list_display = ("device", "device_user_id", "device_name", "mapped_employee",
                    "validity", "is_active", "suggested_match", "last_synced_at")
    list_filter = (UnmappedFilter, "device", "is_active")
    search_fields = ("device_user_id", "device_name", "user__first_name", "user__last_name",
                     "user__email", "user__employee_id")
    autocomplete_fields = ("user",)
    actions = ("unmap_selected",)
    readonly_fields = ("first_seen_at", "last_synced_at", "mapped_at", "mapped_by",
                       "superseded_by", "created_at", "updated_at", "suggested_match")
    list_select_related = ("device", "user")
    fieldsets = (
        (None, {"fields": ("device", "device_user_id", "user", "is_active")}),
        ("Validity window", {
            "fields": ("effective_from", "effective_until", "superseded_by"),
            "description": "Device IDs get reassigned when staff leave. This window is what "
                           "stops a backfill from claiming the previous occupant's attendance.",
        }),
        ("Device roster snapshot", {"fields": ("device_name", "privilege", "card", "group_id",
                                               "suggested_match")}),
        ("Audit", {"fields": ("mapped_at", "mapped_by", "first_seen_at", "last_synced_at",
                              "created_at", "updated_at")}),
    )

    @admin.display(description="Employee", ordering="user__first_name")
    def mapped_employee(self, obj):
        if not obj.user:
            return format_html("<span style='color:#b91c1c'>— unmapped —</span>")
        return f"{obj.user.get_full_name()} ({obj.user.employee_id or 'no code'})"

    @admin.display(description="Valid")
    def validity(self, obj):
        if obj.effective_from and obj.effective_until:
            return f"{obj.effective_from} → {obj.effective_until}"
        if obj.effective_from:
            return f"{obj.effective_from} → open"
        if obj.effective_until:
            return f"… → {obj.effective_until}"
        return format_html("<span style='color:#b45309'>unbounded</span>")

    @admin.display(description="Suggested match")
    def suggested_match(self, obj):
        """Top suggestion, shown for information only — never auto-applied."""
        if obj.user_id or not obj.pk:
            return "—"
        top = suggest_for_mapping(obj, limit=1)
        if not top:
            return format_html("<span style='color:#6b7280'>no candidate — map manually</span>")
        best = top[0]
        return format_html(
            "{} <span style='color:#6b7280'>({}, {:.0%})</span>",
            best["user"].get_full_name(), best["match_type"], best["score"],
        )

    @admin.action(description="Unmap selected (soft — retires the row, keeps history)")
    def unmap_selected(self, request, queryset):
        unmapped = 0
        detached_total = 0
        for mapping in queryset.filter(user__isnull=False):
            _, detached = unmap_employee(mapping, actor=request.user, request=request)
            unmapped += 1
            detached_total += detached
        self.message_user(
            request,
            f"{unmapped} mapping(s) unmapped; {detached_total} punch(es) detached and "
            f"queued for re-derivation. No rows were deleted.",
            level=messages.WARNING if unmapped else messages.INFO,
        )


@admin.register(AttendancePunch)
class AttendancePunchAdmin(admin.ModelAdmin):
    """Read-only: punches are an append-only audit trail.

    Corrections belong on the derived attendance.Attendance row (HR manual
    entry), never on the raw device record — editing these would break the
    traceability the whole design rests on.
    """
    list_display = ("timestamp", "employee_display", "device", "punch_label",
                    "source", "is_processed")
    list_filter = ("device", "punch", "source", "is_processed", "local_date")
    search_fields = ("employee_device_id", "employee_name", "user__first_name",
                     "user__last_name", "user__employee_id")
    date_hierarchy = "local_date"
    readonly_fields = [f.name for f in AttendancePunch._meta.fields]
    list_select_related = ("device", "user")

    @admin.display(description="Employee")
    def employee_display(self, obj):
        if obj.user:
            return obj.user.get_full_name()
        return format_html(
            "<span style='color:#b91c1c'>#{} {}</span>",
            obj.employee_device_id, obj.employee_name or "unmapped",
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DeviceSyncLog)
class DeviceSyncLogAdmin(admin.ModelAdmin):
    list_display = ("started_at", "device", "sync_type", "status", "records_received",
                    "records_created", "records_duplicate", "records_unmapped")
    list_filter = ("device", "sync_type", "status", "started_at")
    readonly_fields = [f.name for f in DeviceSyncLog._meta.fields]
    date_hierarchy = "started_at"
    list_select_related = ("device",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
