"""Platform console serializers. READ shapes, and narrow WRITE shapes.

TWO RULES, AND THEY ARE BOTH SECURITY RULES
-------------------------------------------
1. **No ModelSerializer over Organization, Subscription or Payment.** A
   ``ModelSerializer`` writes whatever the model exposes, and these models
   carry columns that must never come from a request: ``status`` (the
   workspace gate), ``subscription_status`` and the other mirror columns,
   ``seat_count``, ``storage_bytes``, ``amount_minor``. Several are already
   ``editable=False`` -- which keeps them out of a ModelSerializer -- but
   relying on that means the protection is a property of a field flag
   somebody could change for an unrelated reason. So the write serializers
   below are explicit field lists, and every one of them is a plain
   ``Serializer``.

2. **Status is never a writable field.** Not here, not anywhere. A
   subscription moves through ``tenancy.services.transition`` and an
   organization through ``tenancy.lifecycle``, which is what writes the audit
   row and refreshes the mirror. The console exposes ACTIONS (suspend,
   activate, extend), not a status dropdown.
"""
import re

from rest_framework import serializers

from .exceptions import TenancyError
from .models import (Organization, OrganizationBranding,
                     OrganizationSettings, Plan)
from .slugs import validate_tenant_slug


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
class PlanPriceReadSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    currency = serializers.CharField(read_only=True)
    amount_minor = serializers.IntegerField(read_only=True)
    effective_from = serializers.DateField(read_only=True)


class PlanReadSerializer(serializers.Serializer):
    """A plan and its CURRENT price, resolved through tenancy.plans.

    The price is a method field rather than a nested model: ``PlanPrice`` rows
    are immutable and superseded by insertion, so "the price" is a question
    about a date and not a foreign key.
    """
    id = serializers.UUIDField(read_only=True)
    code = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)
    interval_months = serializers.IntegerField(read_only=True)
    trial_days = serializers.IntegerField(read_only=True)
    grace_days = serializers.IntegerField(read_only=True)
    billing_mode = serializers.CharField(read_only=True)
    included_seats = serializers.IntegerField(read_only=True, allow_null=True)
    is_public = serializers.BooleanField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    price = serializers.SerializerMethodField()

    def get_price(self, plan):
        from . import plans

        try:
            price = plans.current_price(plan)
        except Exception:  # noqa: BLE001 - NoActivePrice
            return None
        return PlanPriceReadSerializer(price).data


class SubscriptionReadSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    status_display = serializers.CharField(source="get_status_display",
                                           read_only=True)
    plan_code = serializers.CharField(source="plan.code", read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True)
    interval_months = serializers.IntegerField(source="plan.interval_months",
                                               read_only=True)
    amount_minor = serializers.IntegerField(source="plan_price.amount_minor",
                                            read_only=True, allow_null=True)
    currency = serializers.CharField(source="plan_price.currency",
                                     read_only=True, allow_null=True)
    trial_start = serializers.DateField(read_only=True)
    trial_end = serializers.DateField(read_only=True)
    current_period_start = serializers.DateField(read_only=True)
    current_period_end = serializers.DateField(read_only=True)
    grace_until = serializers.DateField(read_only=True)
    renewal_date = serializers.DateField(read_only=True)
    auto_renew = serializers.BooleanField(read_only=True)
    cancelled_at = serializers.DateTimeField(read_only=True)
    cancel_reason = serializers.CharField(read_only=True)
    days_until_expiry = serializers.SerializerMethodField()

    def get_days_until_expiry(self, subscription):
        return subscription.days_until_expiry()


class OrganizationListSerializer(serializers.Serializer):
    """The tenant list row. Deliberately small: this is a table, not a page."""
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    slug = serializers.SlugField(read_only=True)
    status = serializers.CharField(read_only=True)
    status_display = serializers.CharField(source="get_status_display",
                                           read_only=True)
    subscription_status = serializers.CharField(read_only=True)
    subscription_expiry = serializers.DateField(read_only=True)
    plan_code = serializers.SerializerMethodField()
    # The plan's seat allowance, so the usage table can say "7 of 25" without
    # a second request per row. Free, because `list_organizations` already
    # select_related's the plan.
    seats_included = serializers.SerializerMethodField()
    email = serializers.EmailField(read_only=True)
    industry = serializers.CharField(read_only=True)
    country = serializers.CharField(read_only=True)
    seat_count = serializers.IntegerField(read_only=True)
    storage_bytes = serializers.IntegerField(read_only=True)
    is_admitted = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    def get_plan_code(self, organization):
        subscription = getattr(organization, "subscription", None)
        return getattr(getattr(subscription, "plan", None), "code", None)

    def get_seats_included(self, organization):
        subscription = getattr(organization, "subscription", None)
        return getattr(getattr(subscription, "plan", None), "included_seats",
                       None)


class OrganizationDetailSerializer(OrganizationListSerializer):
    document_prefix = serializers.CharField(read_only=True)
    legacy_number_formats = serializers.BooleanField(read_only=True)
    fiscal_calendar = serializers.CharField(read_only=True)
    phone = serializers.CharField(read_only=True)
    address = serializers.CharField(read_only=True)
    timezone = serializers.CharField(read_only=True)
    site_url = serializers.CharField(read_only=True)
    domain = serializers.CharField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class SubscriptionEventSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    event = serializers.CharField(read_only=True)
    event_display = serializers.CharField(source="get_event_display",
                                          read_only=True)
    from_status = serializers.CharField(read_only=True)
    to_status = serializers.CharField(read_only=True)
    from_plan = serializers.CharField(source="from_plan.code", read_only=True,
                                      allow_null=True)
    to_plan = serializers.CharField(source="to_plan.code", read_only=True,
                                    allow_null=True)
    period_start = serializers.DateField(read_only=True)
    period_end = serializers.DateField(read_only=True)
    actor_email = serializers.CharField(source="actor.email", read_only=True,
                                        allow_null=True)
    note = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class PlatformAuditSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    action = serializers.CharField(read_only=True)
    action_display = serializers.CharField(source="get_action_display",
                                           read_only=True)
    organization_slug = serializers.CharField(read_only=True)
    organization_name = serializers.CharField(read_only=True)
    actor_email = serializers.CharField(read_only=True)
    changes = serializers.JSONField(read_only=True)
    note = serializers.CharField(read_only=True)
    ip_address = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class PaymentQueueSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    organization_slug = serializers.CharField(source="organization.slug",
                                              read_only=True)
    organization_name = serializers.CharField(source="organization.name",
                                              read_only=True)
    payment_reference = serializers.CharField(read_only=True)
    plan_code = serializers.CharField(source="plan.code", read_only=True)
    amount_minor = serializers.IntegerField(read_only=True)
    currency = serializers.CharField(read_only=True)
    method = serializers.CharField(read_only=True)
    transaction_id = serializers.CharField(read_only=True)
    paid_at = serializers.DateField(read_only=True)
    status = serializers.CharField(read_only=True)
    submitted_at = serializers.DateTimeField(read_only=True)
    reviewer_email = serializers.CharField(source="reviewer.email",
                                           read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    # The Payment Center's columns (Part 11): who, what, how, and the
    # customer-facing words the last decision left behind.
    plan_name = serializers.CharField(source="plan.name", read_only=True)
    method_display = serializers.CharField(source="get_method_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    submitted_by = serializers.SerializerMethodField()
    payer_note = serializers.CharField(read_only=True)
    has_proof = serializers.SerializerMethodField()
    rejection_reason = serializers.CharField(read_only=True)
    review_message = serializers.CharField(read_only=True)
    verified_at = serializers.DateTimeField(read_only=True)

    def get_has_proof(self, obj):
        return bool(obj.proof)

    def get_submitted_by(self, obj):
        # The stored copy first: under row-level security the console cannot
        # read a tenant's user row to find it.
        return obj.submitted_by_email or getattr(obj.created_by, "email", None)


class BrandingSerializer(serializers.ModelSerializer):
    """Branding IS a ModelSerializer, and that is safe for one reason.

    Every field on ``OrganizationBranding`` is tenant-presentation data --
    there is no gate, no counter and no money on this model -- and
    ``organization`` is excluded so a payload cannot re-point a branding row
    at another tenant. The image fields are read-only here; uploads go
    through their own endpoint because multipart and JSON are different
    requests.
    """
    class Meta:
        model = OrganizationBranding
        exclude = ["id", "organization"]
        read_only_fields = ["logo_primary", "logo_login", "logo_email",
                            "logo_letterhead", "created_at", "updated_at"]


class SettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationSettings
        exclude = ["id", "organization"]
        read_only_fields = ["created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def _hex_colour(label, value):
    """Six hex digits, or nothing.

    The same rule `tenancy.portal._validate_colour` applies to a customer
    editing their own branding, for the same reason: the value is
    interpolated into a CSS custom property, so anything that is not a
    colour is a value that ends up inside a style attribute.
    """
    text = (value or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        raise serializers.ValidationError(
            f"The {label} must be a six-digit hex colour, like #1D4ED8.")
    return text.upper()


class ProvisionSerializer(serializers.Serializer):
    """Everything "create a tenant" needs, and nothing it must not accept.

    ``status`` is absent on purpose -- provisioning decides it (PROVISIONING
    until the bootstrap finishes, then TRIAL). So is every mirror column.
    """
    name = serializers.CharField(max_length=200)
    slug = serializers.SlugField(max_length=63,
                                 validators=[validate_tenant_slug])
    document_prefix = serializers.CharField(max_length=12)
    email = serializers.EmailField()

    industry = serializers.CharField(max_length=80, required=False,
                                     allow_blank=True, default="")
    phone = serializers.CharField(max_length=32, required=False,
                                  allow_blank=True, default="")
    address = serializers.CharField(required=False, allow_blank=True,
                                    default="")
    country = serializers.CharField(max_length=2, required=False, default="NP")
    timezone = serializers.CharField(max_length=64, required=False,
                                     default="Asia/Kathmandu")
    site_url = serializers.URLField(required=False, allow_blank=True,
                                    default="")
    domain = serializers.CharField(max_length=253, required=False,
                                   allow_blank=True, allow_null=True,
                                   default=None)
    fiscal_calendar = serializers.ChoiceField(
        choices=Organization.FiscalCalendar.choices, required=False,
        default=Organization.FiscalCalendar.BS)

    plan_code = serializers.SlugField(required=False, allow_blank=True,
                                      default="")
    trial_days = serializers.IntegerField(required=False, min_value=0,
                                          max_value=365, allow_null=True,
                                          default=None)
    bootstrap = serializers.BooleanField(required=False, default=True)

    admin_email = serializers.EmailField(required=False, allow_blank=True,
                                         default="")
    admin_name = serializers.CharField(max_length=150, required=False,
                                       allow_blank=True, default="")

    # --- branding, set at creation -------------------------------------
    #
    # So an operator hands over a workspace that already looks like the
    # customer's. Before this, a tenant was provisioned on the platform's own
    # colours and the customer had to upload their logo themselves before
    # their staff saw anything of their own -- on the first day, which is the
    # day the branding matters most.
    #
    # All three are OPTIONAL. A tenant created without them inherits the
    # platform's, which is what every tenant did before.
    logo = serializers.ImageField(required=False, allow_null=True)
    favicon = serializers.ImageField(required=False, allow_null=True)
    color_primary = serializers.CharField(max_length=7, required=False,
                                          allow_blank=True, default="")
    color_secondary = serializers.CharField(max_length=7, required=False,
                                            allow_blank=True, default="")

    def validate_color_primary(self, value):
        return _hex_colour("primary colour", value)

    def validate_color_secondary(self, value):
        return _hex_colour("secondary colour", value)

    def validate_slug(self, value):
        if Organization.objects.filter(slug=value).exists():
            raise serializers.ValidationError(
                "That workspace address is already taken.")
        return value

    def validate_domain(self, value):
        """A custom hostname to CLAIM, not to grant.

        THE DEFECT THIS CLOSES. This used to write `Organization.domain`,
        the single unverified column Phase S9 replaced -- and the resolver
        still honours that column, so an operator typing a hostname here was
        granting it outright. A competitor's name, a bank's, or a typo would
        have resolved to this tenant for anybody whose DNS sent them there.

        Phase S9 built the verification this bypassed. The value is now put
        through `tenancy.domains.validate` (the same rules the customer's own
        page uses) and claimed after provisioning, so it resolves to nothing
        until DNS proves the customer owns it. The tenant's SUBDOMAIN works
        immediately either way, which is what "open under its assigned
        domain" needs.
        """
        value = (value or "").strip().lower() or None
        if not value:
            return None
        from . import domains

        try:
            return domains.validate(value)
        except TenancyError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_plan_code(self, value):
        if value and not Plan.objects.filter(code=value, is_active=True).exists():
            raise serializers.ValidationError(
                f"No active plan with code '{value}'.")
        return value


class OrganizationUpdateSerializer(serializers.Serializer):
    """The editable subset. See ``console.EDITABLE_FIELDS`` for what is not."""
    name = serializers.CharField(max_length=200, required=False)
    email = serializers.EmailField(required=False)
    phone = serializers.CharField(max_length=32, required=False,
                                  allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    industry = serializers.CharField(max_length=80, required=False,
                                     allow_blank=True)
    country = serializers.CharField(max_length=2, required=False)
    timezone = serializers.CharField(max_length=64, required=False)
    site_url = serializers.URLField(required=False, allow_blank=True)
    domain = serializers.CharField(max_length=253, required=False,
                                   allow_blank=True, allow_null=True)
    fiscal_calendar = serializers.ChoiceField(
        choices=Organization.FiscalCalendar.choices, required=False)


class ReasonSerializer(serializers.Serializer):
    """A required, non-empty reason. Used by suspend and cancel.

    Required because these are the two actions a customer phones about, and
    "why" is the first question. An optional reason field is an empty reason
    field.
    """
    reason = serializers.CharField(max_length=2000)

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError("A reason is required.")
        return value.strip()


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=2000, required=False,
                                 allow_blank=True, default="")


class ArchiveSerializer(serializers.Serializer):
    """A required reason. Same argument as ReasonSerializer, stricter subject.

    An archive is the action a customer is most likely to query months later
    -- "why can nobody sign in, and where is our data" -- so the answer has
    to have been written down at the time.
    """
    reason = serializers.CharField(max_length=2000)

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError("An archive reason is required.")
        return value.strip()


class RestoreSerializer(NoteSerializer):
    """Where to land, optionally.

    Omitted means "where it was when it was archived", which is the faithful
    answer and the default. It is offered because of one real case: a tenant
    archived while SUSPENDED whose dispute has since been settled should come
    back ACTIVE rather than straight into another lockout.
    """
    to_status = serializers.ChoiceField(
        choices=[], required=False, allow_null=True, allow_blank=True,
        default=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Built here rather than at import time: naming the enum at module
        # level would import models while this module is still loading.
        from .models import Organization

        self.fields["to_status"].choices = [
            (value, label) for value, label in Organization.Status.choices
            if value != Organization.Status.ARCHIVED
        ]


class ExportRequestSerializer(serializers.Serializer):
    contents = serializers.ChoiceField(choices=[], required=False,
                                       default=None, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import TenantExport

        self.fields["contents"].choices = TenantExport.Contents.choices


class TenantExportSerializer(serializers.Serializer):
    """The receipt. Deliberately does NOT include the manifest.

    A manifest lists every table, every media file and every checksum, which
    for a real tenant is megabytes of JSON -- fine inside the bundle, wrong in
    a list response. It has its own endpoint.
    """
    id = serializers.UUIDField(read_only=True)
    organization_slug = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    status_display = serializers.CharField(source="get_status_display",
                                           read_only=True)
    contents = serializers.CharField(read_only=True)
    requested_by_email = serializers.CharField(read_only=True)
    row_count = serializers.IntegerField(read_only=True)
    media_count = serializers.IntegerField(read_only=True)
    size_bytes = serializers.IntegerField(read_only=True)
    sha256 = serializers.CharField(read_only=True)
    error = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    completed_at = serializers.DateTimeField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)
    downloaded_at = serializers.DateTimeField(read_only=True)
    download_count = serializers.IntegerField(read_only=True)
    is_downloadable = serializers.BooleanField(read_only=True)
    tables = serializers.SerializerMethodField()

    def get_tables(self, export):
        """Row counts per table, which is the summary worth showing.

        From the manifest the platform kept, so it still answers after the
        bundle has expired -- which is the point of storing the manifest on
        the row as well as inside the zip.
        """
        manifest = export.manifest or {}
        return {label: entry.get("rows", 0)
                for label, entry in (manifest.get("tables") or {}).items()}


class PlanChangeSerializer(NoteSerializer):
    plan_code = serializers.SlugField()
    months = serializers.IntegerField(required=False, min_value=1,
                                      max_value=120, allow_null=True,
                                      default=None)

    def validate_plan_code(self, value):
        if not Plan.objects.filter(code=value, is_active=True).exists():
            raise serializers.ValidationError(
                f"No active plan with code '{value}'.")
        return value


class TrialSerializer(NoteSerializer):
    plan_code = serializers.SlugField(required=False, allow_blank=True,
                                      default="")
    days = serializers.IntegerField(required=False, min_value=1,
                                    max_value=365, allow_null=True,
                                    default=None)


class ExtendSerializer(NoteSerializer):
    months = serializers.IntegerField(min_value=1, max_value=120)


class OrganizationStatusSerializer(NoteSerializer):
    """The operator's direct lifecycle override (Part 6).

    The ONLY place a status arrives from a request, and it still goes through
    ``tenancy.lifecycle``, which refuses an illegal move.
    """
    status = serializers.ChoiceField(choices=Organization.Status.choices)


class BrandingAssetSerializer(serializers.Serializer):
    """One uploaded branding image.

    The choices come from ``console``, not a literal, so the two cannot drift:
    the console refuses anything outside its own allow-list anyway, and a
    serializer offering a field the console rejects would answer 400 with a
    message about the wrong thing.
    """
    field = serializers.ChoiceField(choices=[])
    file = serializers.ImageField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .console import BRANDING_ASSETS, ORGANIZATION_ASSETS

        self.fields["field"].choices = sorted(
            BRANDING_ASSETS | ORGANIZATION_ASSETS)
