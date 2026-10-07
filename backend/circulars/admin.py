from django.contrib import admin

from .models import (
    Circular, CircularAcknowledgement, CircularAttachment, CircularAuditLog,
    CircularBroadcast, CircularReadLog, CircularRecipient, CircularWorkflowStep,
)


@admin.register(Circular)
class CircularAdmin(admin.ModelAdmin):
    list_display = ("circular_number", "subject", "status", "classification",
                    "issue_date", "created_by")
    list_filter = ("status", "classification", "category", "priority",
                   "acknowledgement_required")
    search_fields = ("circular_number", "subject", "external_reference")
    # Everything about a circular is set through the workflow engine; the admin is
    # for inspection. Editing status here would bypass every guard the engine holds.
    readonly_fields = ("circular_number", "status", "issued_at", "issued_by",
                       "broadcast_at", "archived_at", "created_at", "updated_at")


@admin.register(CircularBroadcast)
class CircularBroadcastAdmin(admin.ModelAdmin):
    list_display = ("circular", "sequence", "audience", "recipient_count",
                    "broadcast_by_name", "broadcast_at")
    list_filter = ("audience",)


@admin.register(CircularRecipient)
class CircularRecipientAdmin(admin.ModelAdmin):
    list_display = ("circular", "user_name", "department_label", "notified_at")
    search_fields = ("user_name", "circular__circular_number")


admin.site.register(CircularWorkflowStep)
admin.site.register(CircularReadLog)
admin.site.register(CircularAcknowledgement)
admin.site.register(CircularAttachment)
admin.site.register(CircularAuditLog)
