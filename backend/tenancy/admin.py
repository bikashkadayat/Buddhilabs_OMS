"""Django admin for the tenancy records.

READ-MOSTLY ON PURPOSE. Four things are not editable here, and each would
otherwise undo a guarantee the models exist to provide:

* ``Organization.status`` -- the workspace gate (Phase S6). It moves through
  ``tenancy.lifecycle``, which checks the move is legal and records who made
  it; ``Organization.save()`` raises on a direct assignment, so leaving the
  field editable here would mean a 500 on every attempt rather than an input
  that is visibly disabled. The console's lifecycle actions are the way.
* ``Subscription.status`` -- moving it must go through
  ``tenancy.services.transition()``, which also writes the audit event and
  refreshes the mirror. The model's save() would raise anyway; making the field
  read-only turns a 500 into a disabled input.
* ``PlanPrice`` rows -- immutable. A price is superseded by adding a row, never
  by editing one, or every subscription citing it is retroactively restated.
* ``Payment`` amounts and verification fields -- a payment is decided through
  ``tenancy.payments``, which enforces the state machine and the idempotency
  guard that stops one receipt buying two terms.
"""
from django.contrib import admin

from .models import (Organization, OrganizationBranding, OrganizationSettings,
                     Payment, PaymentInstruction, Plan, PlanPrice,
                     PlatformAuditLog, Subscription, SubscriptionEvent)

MIRROR_FIELDS = ("subscription_status", "subscription_start",
                 "subscription_expiry", "seat_count", "storage_bytes")

# Phase S6. The gate and the operator's override of it.
LIFECYCLE_FIELDS = ("status", "status_override", "status_overridden_at",
                    "status_override_reason")


class OrganizationSettingsInline(admin.StackedInline):
    model = OrganizationSettings
    can_delete = False
    extra = 0


class OrganizationBrandingInline(admin.StackedInline):
    model = OrganizationBranding
    can_delete = False
    extra = 0


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "subscription_status",
                    "subscription_expiry", "document_prefix")
    list_filter = ("status", "subscription_status", "country")
    search_fields = ("name", "slug", "email", "domain")
    readonly_fields = (MIRROR_FIELDS + LIFECYCLE_FIELDS
                       + ("created_at", "updated_at"))
    inlines = [OrganizationSettingsInline, OrganizationBrandingInline]


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "interval_months", "billing_mode",
                    "trial_days", "grace_days", "is_active", "is_public")
    list_filter = ("is_active", "is_public", "billing_mode")
    search_fields = ("code", "name")


@admin.register(PlanPrice)
class PlanPriceAdmin(admin.ModelAdmin):
    list_display = ("plan", "currency", "amount_minor", "effective_from",
                    "effective_until")
    list_filter = ("plan", "currency")

    def has_change_permission(self, request, obj=None):
        """A price is append-only. Supersede it by adding a row."""
        return False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("organization", "plan", "status", "current_period_start",
                    "current_period_end", "grace_until")
    list_filter = ("status", "plan")
    search_fields = ("organization__name", "organization__slug")
    readonly_fields = ("status", "created_at", "updated_at")


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(admin.ModelAdmin):
    list_display = ("organization", "event", "from_status", "to_status",
                    "effective_at", "actor")
    list_filter = ("event",)
    search_fields = ("organization__name", "organization__slug", "note")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        """Append-only: an audit trail that can be edited is not one."""
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("payment_reference", "organization", "plan", "currency",
                    "amount_minor", "method", "status", "verified_by")
    list_filter = ("status", "method", "currency")
    search_fields = ("payment_reference", "transaction_id",
                     "organization__name", "organization__slug")
    readonly_fields = ("payment_reference", "amount_minor", "currency",
                       "status", "verified_by", "verified_at",
                       "submitted_at", "review_started_at",
                       "created_at", "updated_at")


@admin.register(PaymentInstruction)
class PaymentInstructionAdmin(admin.ModelAdmin):
    list_display = ("label", "method", "is_active", "sort_order")
    list_filter = ("method", "is_active")


@admin.register(PlatformAuditLog)
class PlatformAuditLogAdmin(admin.ModelAdmin):
    """The platform's record of what it did to a tenant. Read-only, like the
    model itself -- ``save()`` refuses an update and ``delete()`` refuses
    outright, so every permission below turns an exception into a UI that does
    not offer the action."""
    list_display = ("created_at", "action", "organization_slug", "actor_email",
                    "note")
    list_filter = ("action",)
    search_fields = ("organization_slug", "organization_name", "actor_email",
                     "note")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
