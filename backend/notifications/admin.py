from django.contrib import admin

from .models import Notification, NotificationLog, NotificationPreference


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("category", "recipient", "title", "is_read", "read_at",
                    "is_email_sent", "created_at")
    list_filter = ("category", "is_read", "is_email_sent", "created_at")
    search_fields = ("title", "recipient__email", "idempotency_key")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "read_at")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "category", "in_app_enabled", "email_enabled")
    list_filter = ("category", "in_app_enabled", "email_enabled")


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    """
    The outbound-email audit trail.

    The model has existed and been written to all along; it simply was not
    registered, so the record was there and nobody could read it. An audit trail
    that cannot be inspected is not one.

    READ-ONLY BY CONSTRUCTION. These rows are evidence of what the system did.
    Editing one would make it evidence of nothing, so no add, no change, no
    delete — the only way a row appears here is a real send attempt.
    """
    list_display = ("created_at", "status", "module", "reference",
                    "recipient_email", "category", "subject", "short_error")
    list_filter = ("status", "category", "created_at")
    search_fields = ("recipient_email", "subject", "object_id", "reference", "error")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    @admin.display(description="Error")
    def short_error(self, obj):
        return (obj.error or "")[:80]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
