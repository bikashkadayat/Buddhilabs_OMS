"""Django admin for attendance and the policy engine.

The ``attendance`` app had no ``admin.py`` at all before this (audit gap G13).
"""
from django.contrib import admin

from .models import (
    Attendance,
    AttendanceCorrectionRequest,
    AttendancePolicy,
    EmployeeShift,
    PolicyAssignment,
    Shift,
    WFHRequest,
)


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("employee", "date", "status", "check_in", "check_out",
                    "working_hours", "overtime_hours", "late_minutes",
                    "comp_off_days", "source")
    list_filter = ("status", "source", "marked_by", "is_wfh", "comp_off_eligible", "date")
    search_fields = ("employee__first_name", "employee__last_name", "employee__email")
    date_hierarchy = "date"
    autocomplete_fields = ("employee",)
    # Derived by the engine from raw punches and policy; editing them here would
    # be silently overwritten by the next re-derivation.
    readonly_fields = ("first_punch_at", "last_punch_at", "punch_count",
                       "regular_hours", "overtime_hours", "late_minutes",
                       "comp_off_days", "comp_off_eligible",
                       "applied_policy", "applied_shift", "created_at", "updated_at")


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "start_time", "end_time", "grace_minutes", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    # crosses_midnight is intentionally editable so the rejection is visible and
    # explained rather than the field being mysteriously absent.


@admin.register(AttendancePolicy)
class AttendancePolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "office_start_time", "late_after_time",
                    "half_day_after_time", "half_day_hours", "full_day_hours",
                    "overtime_threshold_hours", "comp_off_enabled", "default_shift")
    list_filter = ("is_active", "comp_off_enabled", "deduct_breaks")
    search_fields = ("name", "description")
    fieldsets = (
        (None, {"fields": ("name", "description", "is_active", "default_shift")}),
        ("Timing", {
            "fields": ("office_start_time", "late_after_time", "half_day_after_time",
                       "grace_minutes", "absent_cutoff_time"),
            "description": "Check-in at or before 'late after' is Present; after it is "
                           "Late; at or after 'half day after' is Half Day. Clear "
                           "'late after' to fall back to office start + grace minutes.",
        }),
        ("Hours", {"fields": ("half_day_hours", "full_day_hours", "deduct_breaks")}),
        ("Overtime", {"fields": ("overtime_threshold_hours", "overtime_min_minutes"),
                      "description": "Leave the threshold empty to disable overtime."}),
        ("Compensatory off", {"fields": (
            "comp_off_enabled", "comp_off_on_saturday", "comp_off_on_holiday",
            "comp_off_min_hours", "comp_off_half_day_hours", "comp_off_full_day_hours")}),
    )


@admin.register(PolicyAssignment)
class PolicyAssignmentAdmin(admin.ModelAdmin):
    list_display = ("policy", "scope", "department", "user",
                    "effective_from", "effective_until")
    list_filter = ("scope", "policy")
    # department is a plain select: leaves.Department has no admin registration
    # to autocomplete against, and adding one is outside this phase's scope.
    autocomplete_fields = ("user", "policy")
    search_fields = ("policy__name", "user__email", "department__name")


@admin.register(EmployeeShift)
class EmployeeShiftAdmin(admin.ModelAdmin):
    list_display = ("user", "shift", "effective_from", "effective_until")
    list_filter = ("shift",)
    autocomplete_fields = ("user", "shift")
    search_fields = ("user__email", "user__first_name", "user__last_name")


@admin.register(AttendanceCorrectionRequest)
class AttendanceCorrectionRequestAdmin(admin.ModelAdmin):
    list_display = ("employee", "attendance_date", "status", "manager",
                    "hr_actor", "applied_at")
    list_filter = ("status", "attendance_date")
    autocomplete_fields = ("employee", "manager", "hr_actor", "rejected_by", "attendance")
    search_fields = ("employee__email", "employee__first_name", "employee__last_name",
                     "reason")
    date_hierarchy = "attendance_date"
    # The snapshot is the only route back from an applied correction; editing it
    # here would quietly destroy the undo path.
    readonly_fields = ("previous_check_in", "previous_check_out", "previous_status",
                       "previous_source", "had_attendance_row", "applied_at",
                       "reverted_at", "created_at", "updated_at")


@admin.register(WFHRequest)
class WFHRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "start_date", "end_date", "status", "reviewed_by", "reviewed_at")
    list_filter = ("status",)
    autocomplete_fields = ("user", "reviewed_by")
    search_fields = ("user__email", "user__first_name", "user__last_name", "reason")
    date_hierarchy = "start_date"
