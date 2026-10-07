"""
Admin registration for the minute module.

The taxonomy is editable - that is the point of it being rows rather than an enum. The
transactional models are registered read-mostly: a minute's status is moved by the
workflow engine, and an admin who edits it by hand leaves the audit trail describing a
history that did not happen.
"""
from django.contrib import admin

from .models import (
    Minute, MinuteAuditLog, MinuteInvolvement, MinuteParticipant, MinuteType,
)


@admin.register(MinuteType)
class MinuteTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "ordering", "is_active"]
    list_editable = ["ordering", "is_active"]
    search_fields = ["name", "code"]


class ParticipantInline(admin.TabularInline):
    model = MinuteParticipant
    extra = 0
    fields = ["user", "attendance", "ack_status", "acknowledged_at"]
    readonly_fields = ["acknowledged_at"]


@admin.register(Minute)
class MinuteAdmin(admin.ModelAdmin):
    list_display = ["minute_number", "display_title", "minute_type", "meeting_date",
                    "status", "created_by", "archived_at"]
    list_filter = ["status", "minute_type", "meeting_date"]
    search_fields = ["minute_number", "subject", "reference_number"]
    readonly_fields = ["minute_number", "created_at", "updated_at",
                       "sent_for_review_at", "acknowledgement_opened_at",
                       "archived_at"]
    inlines = [ParticipantInline]


@admin.register(MinuteAuditLog)
class MinuteAuditLogAdmin(admin.ModelAdmin):
    list_display = ["minute", "action", "actor_display", "created_at"]
    list_filter = ["action"]
    search_fields = ["minute__minute_number", "actor_name", "remarks"]
    # The trail is append-only. Read-only here as well, so the admin cannot rewrite
    # the evidence the module produces.
    readonly_fields = [f.name for f in MinuteAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(MinuteInvolvement)
