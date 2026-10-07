"""Tenancy, subscription and payment records.

This app sits BELOW every business module in the dependency graph: it imports
nothing from leaves, memos, tasks, inventory or attendance, and they do not yet
import it. That one-way edge is what lets the foundation land without touching
a single business model -- see tenancy/inventory.py for what the later phases
will attach to it.

THREE RULES ENCODED HERE
------------------------
1. **A price is never updated, only superseded.** ``PlanPrice`` rows are
   immutable; a Subscription pins the exact row it was sold at. Editing a price
   in place would retroactively rewrite what existing customers agreed to pay.

2. **A subscription's status moves only through tenancy.services.transition().**
   Enforced in ``Subscription.save()``, not by convention, because the
   transition is also what writes the audit event and refreshes the mirror
   below.

3. **Organization.subscription_* is a read-only mirror.** The source of truth is
   Subscription. The mirror exists because every single HTTP request must answer
   "is this tenant allowed in?" and that cannot be a join against a billing
   table on the hot path.
"""
import uuid

from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from .exceptions import DirectStatusChangeForbidden
from .slugs import validate_tenant_slug
from .uploads import export_bundle_path, org_asset_path, payment_proof_path

# A 7-character hex colour, or blank for "inherit the platform default".
HEX_COLOR = RegexValidator(
    r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
    "Enter a hex colour such as #1D4ED8.")


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------
class Organization(models.Model):
    """One customer. The tenant boundary every later phase scopes to."""

    class Status(models.TextChoices):
        """The organization lifecycle (Phase S6 Part 6).

            PROVISIONING -> TRIAL -> ACTIVE -> GRACE -> SUSPENDED -> CANCELLED

        GRACE arrived in Phase S6. Before it, a tenant whose subscription had
        lapsed into its grace period showed as ACTIVE on the platform console,
        which is the one state an operator most needs to see: it is the
        difference between "paying" and "about to be locked out, phone them".
        A grace tenant IS still admitted -- that is what a grace period is --
        so this changes what the console displays, not who gets in.
        """
        PROVISIONING = "provisioning", "Provisioning"
        TRIAL = "trial", "Trial"
        ACTIVE = "active", "Active"
        GRACE = "grace", "Grace period"
        SUSPENDED = "suspended", "Suspended"
        CANCELLED = "cancelled", "Cancelled"
        # Phase S6.5. NOT a stronger suspension and not a deletion: a
        # suspension is a dispute, a cancellation is a commercial end, and an
        # archive is "this workspace is closed and its data is being kept".
        # The distinction is operational -- an archived tenant must produce no
        # activity at all, so nothing is derived over it and nothing in it is
        # editable -- and it is also a promise, because the whole point of
        # archiving rather than cancelling is that a restore returns the
        # customer exactly as they were.
        ARCHIVED = "archived", "Archived"

    class FiscalCalendar(models.TextChoices):
        BS = "BS", "Bikram Sambat"
        AD = "AD", "Gregorian"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    # 63 is the maximum length of a DNS label, and this becomes one
    # (<slug>.<base domain>). A longer slug would produce an organization that
    # can be created but never reached.
    slug = models.SlugField(max_length=63, unique=True,
                            validators=[validate_tenant_slug])
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.PROVISIONING, db_index=True)

    logo = models.ImageField(upload_to=org_asset_path, max_length=255,
                              null=True, blank=True)
    favicon = models.ImageField(upload_to=org_asset_path, max_length=255,
                                 null=True, blank=True)

    industry = models.CharField(max_length=80, blank=True, default="")
    address = models.TextField(blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")
    email = models.EmailField(help_text="Billing and platform-notice address.")
    country = models.CharField(max_length=2, default="NP",
                               help_text="ISO 3166-1 alpha-2.")
    timezone = models.CharField(max_length=64, default="Asia/Kathmandu")

    # Replaces the hardcoded "NIFN" / "NIF" prefixes in six document-number
    # generators. NIF keeps "NIFN", so not one existing NIF number changes.
    document_prefix = models.CharField(max_length=12)

    # Which format family this tenant's document numbers use (Phase S2).
    #
    # True for NIF and ONLY NIF: four of the existing formats (MIN-, CIR-,
    # TRF-, DSP-) carry no organisation prefix at all, and adding one would
    # change the numbers NIF's own documents are issued under. A document
    # number is a historical record -- you cannot restate one that has already
    # been printed, signed and filed.
    #
    # Every future tenant gets False, which means one consistent scheme:
    # <DOCUMENT_PREFIX>-<KIND>-<year>-<n>, prefixed throughout. See
    # tenancy/numbering.py. The flag is a deprecation seam, not a permanent
    # fork: NIF can flip it whenever it will accept the modern format for NEW
    # records, and nothing already issued changes either way.
    legacy_number_formats = models.BooleanField(
        default=False,
        help_text="Emit the pre-SaaS document number formats. NIF only.")

    # Which calendar this tenant's document numbers and fiscal periods use.
    # Every number sequence in this codebase currently embeds a Bikram Sambat
    # year (NIFN-TSK-2083-0001); that is a tenant property, not a platform one.
    fiscal_calendar = models.CharField(max_length=2, choices=FiscalCalendar.choices,
                                       default=FiscalCalendar.BS)

    # Per-tenant public base URL, for the QR verification links embedded in
    # PDFs. Replaces the single global settings.SITE_URL.
    site_url = models.URLField(blank=True, default="")

    # A customer-owned hostname (hr.abcschool.edu.np). Modelled now so the
    # resolver can match it; provisioning it is a later phase.
    domain = models.CharField(max_length=253, unique=True, null=True, blank=True)

    # Future-proofing seam: lets one large or compliance-bound tenant be moved
    # to its own database later without touching application code.
    database_alias = models.CharField(max_length=40, default="default")

    # ---- read-only mirror of Subscription (see module docstring, rule 3) ----
    # editable=False keeps these out of every ModelForm and ModelSerializer, so
    # no admin screen can drift them away from the source of truth. They are
    # written only by tenancy.services.sync_subscription_mirror().
    subscription_status = models.CharField(max_length=20, default="trial",
                                           editable=False, db_index=True)
    subscription_start = models.DateField(null=True, blank=True, editable=False)
    subscription_expiry = models.DateField(null=True, blank=True, editable=False,
                                           db_index=True)

    # ---- the operator's override of the derived status (Phase S6) ----
    #
    # ``status`` is normally DERIVED from the subscription by
    # tenancy.services.sync_subscription_mirror. This flag says "an operator
    # set it deliberately, and the subscription must not move it back".
    #
    # It exists for one case that is not hypothetical: a customer suspended
    # for abuse, whose payment then clears. Without the flag, the renewal
    # re-opens the workspace -- because ACTIVE is a legal successor of
    # SUSPENDED, so nothing refuses it and nothing is logged as having
    # decided it. The operator's suspension would be undone by a bank
    # transfer.
    #
    # editable=False: set only by tenancy.lifecycle, like the mirror columns
    # above and for the same reason.
    status_override = models.BooleanField(
        default=False, editable=False,
        help_text="The status was set by a platform operator; the "
                  "subscription must not derive over it.")
    status_overridden_at = models.DateTimeField(null=True, blank=True,
                                                 editable=False)
    status_override_reason = models.CharField(max_length=300, blank=True,
                                               default="", editable=False)

    # Phase S6.5: the archive, and what makes it undoable.
    #
    # editable=False on all three: written only by tenancy.archive, for the
    # same reason the mirror and override columns are -- a workspace that
    # could be un-archived by a serializer would un-archive without a record
    # of who decided it.
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    archived_reason = models.CharField(max_length=300, blank=True, default="",
                                       editable=False)
    # WHY THE PREVIOUS STATUS IS STORED RATHER THAN DERIVED LATER. A restore
    # has to put the customer back where they were, and after the fact there
    # is nothing to work that out from: "they were probably active" is a guess
    # about somebody else's billing. One column removes the guess.
    status_before_archive = models.CharField(max_length=20, blank=True,
                                              default="", editable=False)

    # Maintained counters, for plan-limit checks and the platform dashboard.
    seat_count = models.PositiveIntegerField(default=0, editable=False)
    storage_bytes = models.BigIntegerField(default=0, editable=False)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True,
                                   related_name="organizations_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.slug})"

    # Remembers the hostnames this row answered to when it was loaded, so
    # tenancy.resolver.forget() can evict the OLD cache entry after a rename
    # instead of only the new one. Same technique as Subscription.from_db.
    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_slug = instance.slug
        instance._loaded_domain = instance.domain
        instance._loaded_status = instance.status
        return instance

    def save(self, *args, **kwargs):
        """Persist, then evict this organization's cached host mappings.

        The resolver's hot path (``tenancy.resolver.resolve_id``) re-validates
        a cached mapping against the slug and domain carried IN the cache
        entry, so it needs no row fetch -- which means a rename would otherwise
        go unnoticed for up to the cache TTL. Evicting here closes that window
        at the only moment a rename can happen.

        ALSO REFUSES AN UNAUTHORISED STATUS CHANGE (Phase S6 Part 6).

        ``status`` is what decides whether anybody may use this workspace, and
        it is reachable from a DRF serializer, the Django admin and a shell.
        A bare ``org.status = "active"; org.save()`` would let a tenant back
        in with no record of who did it, no legality check against the
        lifecycle, and no eviction of the resolver cache the request path
        gates on. So the lifecycle module is the only permitted mover -- the
        same enforcement as ``Subscription.save()``, for the same reason.
        """
        loaded = getattr(self, "_loaded_status", None)
        authorised = getattr(self, "_status_change_authorised", False)
        if loaded is not None and loaded != self.status and not authorised:
            from .lifecycle import IllegalOrganizationTransition

            raise IllegalOrganizationTransition(
                f"Refusing to move organization {self.slug} from '{loaded}' "
                f"to '{self.status}' by direct assignment. Use "
                f"tenancy.lifecycle.transition_organization(), which checks "
                f"the move is legal and records who made it.")
        super().save(*args, **kwargs)
        from . import resolver

        resolver.forget(self)
        self._loaded_slug = self.slug
        self._loaded_domain = self.domain
        # Re-arm the guard for the next save on this same instance.
        self._loaded_status = self.status
        self._status_change_authorised = False

    @property
    def is_admitted(self):
        """May this tenant's users reach the application at all?

        TRIAL and ACTIVE yes; a tenant inside its grace period is also admitted
        (that is what a grace period is for). SUSPENDED and CANCELLED no.
        PROVISIONING no -- the workspace is not finished being built.
        ARCHIVED no, and that one is not negotiable: an archived tenant must
        generate no activity whatsoever, because every byte of it is being
        preserved for a restore that has to be faithful.
        """
        if self.status in (self.Status.SUSPENDED, self.Status.CANCELLED,
                           self.Status.PROVISIONING, self.Status.ARCHIVED):
            return False
        # GRACE is deliberately absent from that list. See Status.GRACE.
        return self.subscription_status in (
            Subscription.Status.TRIAL, Subscription.Status.ACTIVE,
            Subscription.Status.GRACE)


class OrganizationSettings(models.Model):
    """Per-tenant operational policy that is currently process configuration.

    Everything here is presently an environment variable in config/settings.py,
    applied platform-wide. They are per-COMPANY policy, not per-DEPLOYMENT
    configuration: "the office opens at 10:00" and "escalate to HR after seven
    days" are answers that differ per customer.

    Every field is nullable or blank, and resolution is:

        OrganizationSettings value -> settings.py default -> hardcoded fallback

    so a tenant that customises nothing behaves exactly as the platform does
    today. That is what keeps NIF's behaviour unchanged by this phase.
    """
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE,
                                         related_name="settings")

    # --- attendance policy (ATTENDANCE_* in config/settings.py) ---
    office_start = models.TimeField(null=True, blank=True)
    full_day_hours = models.DecimalField(max_digits=4, decimal_places=2,
                                          null=True, blank=True)
    half_day_hours = models.DecimalField(max_digits=4, decimal_places=2,
                                          null=True, blank=True)
    absent_cutoff = models.TimeField(null=True, blank=True)
    tracking_start = models.DateField(null=True, blank=True)

    # --- office geofence ---
    office_name = models.CharField(max_length=120, blank=True, default="")
    office_lat = models.DecimalField(max_digits=9, decimal_places=6,
                                      null=True, blank=True)
    office_lng = models.DecimalField(max_digits=9, decimal_places=6,
                                      null=True, blank=True)
    office_radius_m = models.PositiveIntegerField(null=True, blank=True)
    max_accuracy_m = models.PositiveIntegerField(null=True, blank=True)
    require_location = models.BooleanField(null=True, blank=True)

    # --- how this tenant takes attendance ---
    #
    # Biometric devices are hardware a customer may or may not own, and
    # app-based check-in is a capability they may or may not want their staff
    # to have. Both can be true. Null means "whatever the platform default
    # is", which is how every other column on this model behaves.
    #
    # PER TENANT, not a deployment setting, because this is the one attendance
    # question where customers genuinely differ: a hospital with turnstiles
    # wants biometric only, a field NGO has no hardware at all, and a school
    # with a gate reader and travelling staff wants both.
    class AttendanceMode(models.TextChoices):
        BIOMETRIC_ONLY = "biometric_only", "Biometric devices only"
        APP_ONLY = "app_only", "App check-in only"
        BOTH = "both", "Biometric devices and app check-in"

    attendance_mode = models.CharField(
        max_length=20, choices=AttendanceMode.choices, blank=True, default="")

    # --- task escalation ladder (TASK_ESCALATION_* in config/settings.py) ---
    escalation_supervisor_days = models.PositiveSmallIntegerField(null=True, blank=True)
    escalation_hr_days = models.PositiveSmallIntegerField(null=True, blank=True)
    escalation_management_days = models.PositiveSmallIntegerField(null=True, blank=True)
    review_pending_days = models.PositiveSmallIntegerField(null=True, blank=True)

    # --- analytics ---
    # NIF's suppression floor is 3. A four-person tenant has no publishable
    # department analytics at all, so this has to be per-tenant or the platform
    # exposes individuals.
    min_department_sample = models.PositiveSmallIntegerField(null=True, blank=True)

    # --- notification defaults (Phase S6 Part 4) ---
    #
    # What a user's notification preference is BEFORE they ever open the
    # preferences screen. Previously that answer was a literal in
    # notifications.dispatcher.get_preference -- in-app on, email on, weekly
    # digest off -- applied to every tenant on the platform.
    #
    # NULL means "inherit the platform default", so NIF (whose row is all
    # NULLs) behaves exactly as it does today. Provisioning writes explicit
    # values for a new tenant, because an operator cannot change a default
    # they cannot see.
    notify_in_app_default = models.BooleanField(null=True, blank=True)
    notify_email_default = models.BooleanField(null=True, blank=True)
    notify_digest_default = models.BooleanField(null=True, blank=True)

    # --- Phase S7: the onboarding checklist's own state ---
    #
    # WHY IT LIVES HERE AND NOT IN A NEW TENANT-SCOPED MODEL. A new
    # tenant-owned table costs an `organization` column, a scoped manager
    # pair, an entry in `tenancy.inventory`, an RLS policy migration and a
    # composite unique constraint -- the whole Phase S2-S5 apparatus -- to
    # hold two facts about a wizard. `OrganizationSettings` is already
    # one-per-tenant, already platform-side, and already the home of
    # "per-company preferences", which is what a dismissed wizard is.
    #
    # Two steps genuinely cannot be measured from data: "invite your team" has
    # no row that proves it happened, and a customer who decides the five
    # seeded departments are right has nothing to create. Those are ticked by
    # hand, and this is where the ticks go.
    onboarding_dismissed_at = models.DateTimeField(null=True, blank=True)
    onboarding_steps = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "organization settings"

    def __str__(self):
        return f"settings for {self.organization.slug}"


class OrganizationBranding(models.Model):
    """What a tenant's workspace, documents and email look like.

    Blank/null everywhere means "inherit the platform default", so an
    organization that customises nothing is indistinguishable from the product
    as it ships. The three ``*_html`` fields are tenant-authored and MUST be
    passed through common.html_sanitizer before they are rendered anywhere.
    """
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE,
                                         related_name="branding")

    display_name = models.CharField(max_length=200, blank=True, default="")
    logo_primary = models.ImageField(upload_to=org_asset_path, max_length=255,
                                      null=True, blank=True)
    logo_login = models.ImageField(upload_to=org_asset_path, max_length=255,
                                    null=True, blank=True)
    logo_email = models.ImageField(upload_to=org_asset_path, max_length=255,
                                    null=True, blank=True)
    logo_letterhead = models.ImageField(upload_to=org_asset_path, max_length=255,
                                         null=True, blank=True)

    color_primary = models.CharField(max_length=7, blank=True, default="",
                                      validators=[HEX_COLOR])
    # Phase S9 Part 1. A third colour, for the one thing the first two cannot
    # express: primary is the brand, secondary supports it, and an accent is
    # what a call to action is painted with. Blank means "inherit", like
    # every other field here.
    color_accent = models.CharField(
        max_length=7, blank=True, default="",
        help_text="Hex, e.g. #F59E0B. Used for buttons and highlights.")
    color_secondary = models.CharField(max_length=7, blank=True, default="",
                                        validators=[HEX_COLOR])

    login_tagline = models.CharField(max_length=200, blank=True, default="")
    dashboard_welcome = models.CharField(max_length=300, blank=True, default="")
    report_footer_text = models.CharField(max_length=300, blank=True, default="")

    # Tenant-authored HTML. Sanitise on the way out, every time.
    email_footer_html = models.TextField(blank=True, default="")
    letterhead_header_html = models.TextField(blank=True, default="")
    letterhead_footer_html = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"branding for {self.organization.slug}"

    def public_payload(self):
        """The ONLY branding fields safe to serve before authentication.

        A login page needs a logo and a colour. It does not need -- and an
        unauthenticated prober must not receive -- headcount, plan, status or
        anything else about the tenant. Keeping the allow-list on the model
        means the pre-login endpoint cannot accidentally widen it.
        """
        return {
            "name": self.display_name or self.organization.name,
            "logo_login": self.logo_login.name if self.logo_login else None,
            # On `Organization`, not here -- see console.ORGANIZATION_ASSETS.
            # It is in the PRE-login payload because that is the only moment
            # it can be applied: the browser asks for a tab icon before
            # anybody signs in, and until this was served, a tenant who had
            # uploaded their own favicon still got the platform's.
            "favicon": (self.organization.favicon.name
                        if self.organization.favicon else None),
            "color_primary": self.color_primary,
            "color_secondary": self.color_secondary,
            "login_tagline": self.login_tagline,
        }


# ---------------------------------------------------------------------------
# Plans and prices
# ---------------------------------------------------------------------------
class Plan(models.Model):
    """What a customer can buy. Shape only -- the money lives in PlanPrice."""

    class BillingMode(models.TextChoices):
        FLAT = "flat", "Flat per organization"
        PER_SEAT = "per_seat", "Per seat"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True, default="")

    # Months, not days: a subscription must expire on the same day of the month
    # it started, which day arithmetic cannot express.
    interval_months = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)])

    trial_days = models.PositiveSmallIntegerField(default=14)
    # Days after expiry during which the tenant is still admitted. A customer
    # whose bank transfer takes three days to clear must not be locked out of
    # their own HR system on the morning of day one.
    grace_days = models.PositiveSmallIntegerField(default=7)

    # Modelled now, unused in V1 (every seeded plan is FLAT). A flat
    # unlimited-seat price cannot serve both a 12-person NGO and a 500-bed
    # hospital, so the columns that make per-seat possible are cheaper to carry
    # now than to migrate in later.
    billing_mode = models.CharField(max_length=10, choices=BillingMode.choices,
                                     default=BillingMode.FLAT)
    included_seats = models.PositiveIntegerField(null=True, blank=True)

    is_public = models.BooleanField(default=True,
                                     help_text="Shown in the plan picker.")
    is_active = models.BooleanField(default=True,
                                     help_text="New subscriptions permitted.")
    sort_order = models.PositiveSmallIntegerField(default=100)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "interval_months"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class PlanPrice(models.Model):
    """IMMUTABLE. Change a price by inserting a new row, never by UPDATE.

    A ``Plan.price`` column that an admin can edit looks like it satisfies "no
    hardcoded pricing", but it retroactively rewrites what existing customers
    agreed to pay -- their invoices, renewal quotes and payment-verification
    amounts all move under them. So prices are append-only and a Subscription
    pins the exact row it was sold at.

    Money is an integer in the currency's minor unit (paisa). Never a float, and
    never a bare Decimal in transport: a major-unit decimal and a minor-unit
    integer are different values, and only one of them survives a round trip
    through JSON intact.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="prices")
    currency = models.CharField(max_length=3, default="NPR")
    amount_minor = models.BigIntegerField(
        validators=[MinValueValidator(0)],
        help_text="Minor units (paisa for NPR). 12345 = NPR 123.45")
    per_extra_seat_minor = models.BigIntegerField(null=True, blank=True,
                                                   validators=[MinValueValidator(0)])

    effective_from = models.DateField()
    effective_until = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["plan", "-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "currency", "effective_from"],
                name="uniq_plan_price_effective"),
            models.CheckConstraint(
                condition=models.Q(effective_until__isnull=True)
                | models.Q(effective_until__gte=models.F("effective_from")),
                name="plan_price_period_ordered"),
        ]

    def __str__(self):
        return f"{self.plan.code} {self.currency} {self.amount_minor} from {self.effective_from}"

    @property
    def amount_major(self):
        """For display only. Never use this for arithmetic."""
        from decimal import Decimal

        return Decimal(self.amount_minor) / Decimal(100)


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------
class Subscription(models.Model):
    """What a tenant currently has, and until when.

    STATUS IS NOT ASSIGNABLE. ``save()`` refuses a status that changed outside
    ``tenancy.services.transition()`` -- see the module docstring, rule 2.
    """

    class Status(models.TextChoices):
        TRIAL = "trial", "Trial"
        ACTIVE = "active", "Active"
        GRACE = "grace", "Grace period"
        SUSPENDED = "suspended", "Suspended"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(Organization, on_delete=models.PROTECT,
                                         related_name="subscription")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT,
                              related_name="subscriptions")
    # The exact price row this was sold at. PROTECT, so a price that any
    # subscription references can never be deleted out from under it.
    plan_price = models.ForeignKey(PlanPrice, on_delete=models.PROTECT,
                                    null=True, blank=True,
                                    related_name="subscriptions")

    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.TRIAL, db_index=True)

    trial_start = models.DateField(null=True, blank=True)
    trial_end = models.DateField(null=True, blank=True)

    current_period_start = models.DateField(null=True, blank=True)
    current_period_end = models.DateField(null=True, blank=True, db_index=True)
    # Stored rather than computed from plan.grace_days, because the nightly job
    # has to ask "whose grace has lapsed?" as an indexed date comparison, not as
    # arithmetic applied row by row across every tenant.
    grace_until = models.DateField(null=True, blank=True, db_index=True)
    renewal_date = models.DateField(null=True, blank=True)

    # False in V1: payment is manual, so nothing can charge a card on renewal.
    auto_renew = models.BooleanField(default=False)

    seats = models.PositiveIntegerField(default=0)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization__name"]

    def __str__(self):
        return f"{self.organization.slug}: {self.plan.code} ({self.status})"

    # -- the direct-assignment guard -------------------------------------
    #
    # from_db remembers what the database said, so save() can compare without
    # issuing a second query. The alternative (re-reading the row in save())
    # would add a SELECT to every subscription write for the same answer.
    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_status = instance.status
        return instance

    def save(self, *args, **kwargs):
        loaded = getattr(self, "_loaded_status", None)
        authorised = getattr(self, "_status_change_authorised", False)
        if loaded is not None and loaded != self.status and not authorised:
            raise DirectStatusChangeForbidden(
                f"Refusing to move subscription {self.pk} from '{loaded}' to "
                f"'{self.status}' by direct assignment. Use "
                f"tenancy.services.transition(), which also writes the "
                f"SubscriptionEvent audit row and refreshes the Organization "
                f"mirror.")
        super().save(*args, **kwargs)
        # Re-arm the guard for the next save on this same instance.
        self._loaded_status = self.status
        self._status_change_authorised = False

    @property
    def is_admitted(self):
        return self.status in (self.Status.TRIAL, self.Status.ACTIVE,
                               self.Status.GRACE)

    def days_until_expiry(self, today=None):
        if self.current_period_end is None:
            return None
        return (self.current_period_end - (today or timezone.localdate())).days


class SubscriptionEvent(models.Model):
    """Append-only record of every lifecycle change. Never updated or deleted.

    Written only by ``tenancy.services.transition()``, in the same transaction
    as the status change it describes. This is the answer to "why is this
    customer suspended, and who did it?".
    """

    class Event(models.TextChoices):
        CREATED = "created", "Created"
        TRIAL_STARTED = "trial_started", "Trial started"
        ACTIVATED = "activated", "Activated"
        RENEWED = "renewed", "Renewed"
        EXTENDED = "extended", "Extended"
        PLAN_CHANGED = "plan_changed", "Plan changed"
        ENTERED_GRACE = "entered_grace", "Entered grace period"
        EXPIRED = "expired", "Expired"
        SUSPENDED = "suspended", "Suspended"
        REACTIVATED = "reactivated", "Reactivated"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE,
                                      related_name="subscription_events")
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE,
                                      related_name="events")
    event = models.CharField(max_length=30, choices=Event.choices, db_index=True)

    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, blank=True, default="")
    from_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="+")
    to_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name="+")

    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)

    # Null for a transition made by the nightly job rather than by a person.
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True,
                               related_name="subscription_events")
    payment = models.ForeignKey("tenancy.Payment", on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name="events")
    note = models.TextField(blank=True, default="")
    effective_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-effective_at"]
        constraints = [
            # A payment may activate a subscription AT MOST ONCE, enforced by
            # the database rather than by a status check that a retry, a double
            # click or two concurrent reviewers could race past. Without this,
            # verifying one payment twice would extend the period twice.
            models.UniqueConstraint(
                fields=["payment"],
                condition=models.Q(event__in=["activated", "renewed", "extended"]),
                name="uniq_activation_per_payment"),
        ]

    def __str__(self):
        return f"{self.organization.slug}: {self.event} @ {self.effective_at:%Y-%m-%d}"


# ---------------------------------------------------------------------------
# Payments (manual verification only -- no gateway in V1)
# ---------------------------------------------------------------------------
class PaymentInstruction(models.Model):
    """Where a customer should send money, as data rather than as a template.

    eSewa IDs, bank account numbers and branch names change. Hardcoding them in
    a template repeats exactly the mistake the no-hardcoded-pricing rule exists
    to prevent, with the added property that a stale account number sends real
    money somewhere nobody is watching.

    ``instructions_html`` is platform-staff-authored and still MUST be passed
    through common.html_sanitizer: platform staff are trusted, but a compromised
    platform account would otherwise reach every tenant.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    method = models.CharField(max_length=20, db_index=True)
    label = models.CharField(max_length=120)

    account_name = models.CharField(max_length=160, blank=True, default="")
    account_number = models.CharField(max_length=64, blank=True, default="")
    bank_name = models.CharField(max_length=160, blank=True, default="")
    branch = models.CharField(max_length=160, blank=True, default="")
    esewa_id = models.CharField(max_length=64, blank=True, default="")
    khalti_id = models.CharField(max_length=64, blank=True, default="")

    qr_image = models.ImageField(upload_to="platform/payment-instructions/",
                                  max_length=255, null=True, blank=True)
    instructions_html = models.TextField(blank=True, default="")

    is_active = models.BooleanField(default=True)
    # Archived: retired for good, kept because past payments name it. Hidden
    # from customers AND from the console's working list; restorable.
    archived_at = models.DateTimeField(null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "label"]

    def __str__(self):
        return f"{self.label} ({self.method})"

    @property
    def state(self):
        if self.archived_at:
            return "archived"
        return "active" if self.is_active else "disabled"


class Payment(models.Model):
    """One manual payment, and its verification state.

    FOUR THINGS HERE ARE SECURITY CONTROLS, NOT CONVENIENCES:

    1. ``amount_minor`` is ``editable=False`` and derived server-side from
       ``plan_price``. If it came from the client, a tenant would declare they
       paid NPR 1 for an annual plan and a busy reviewer would approve it.

    2. ``payment_reference`` is OURS (quoted on the transfer so it can be
       matched); ``transaction_id`` is THEIRS (the bank/eSewa reference). They
       are different fields because they are different facts, and conflating
       them makes reconciliation guesswork.

    3. ``transaction_id`` is unique per organization, so the same bank transfer
       cannot be submitted against two different plan purchases.

    4. ``proof`` is stored OUTSIDE the tenant media tree (see tenancy.uploads):
       it is attacker-supplied content opened by the most privileged user on the
       platform, and it must never be reachable through the tenant signed-URL
       mechanism.
    """

    class Method(models.TextChoices):
        ESEWA = "esewa", "eSewa"
        KHALTI = "khalti", "Khalti"
        FONEPAY = "fonepay", "Fonepay"
        QR = "qr", "QR code"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        CHEQUE = "cheque", "Cheque"
        CASH = "cash", "Cash"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        AWAITING_PROOF = "awaiting_proof", "Awaiting proof"
        SUBMITTED = "submitted", "Proof submitted"
        UNDER_REVIEW = "under_review", "Under review"
        # The reviewer cannot decide without something from the customer --
        # an unreadable screenshot, an amount that does not match. Neither a
        # rejection (nothing is wrong yet) nor a wait (the customer must act).
        NEEDS_INFO = "needs_info", "More information requested"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT,
                                      related_name="payments")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="payments")
    plan_price = models.ForeignKey(PlanPrice, on_delete=models.PROTECT,
                                    related_name="payments")

    # Generated, per-organization. Quoted on the bank transfer / eSewa remark so
    # a received payment can be matched to the tenant that sent it.
    payment_reference = models.CharField(max_length=32, editable=False)

    amount_minor = models.BigIntegerField(editable=False,
                                           validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="NPR", editable=False)

    method = models.CharField(max_length=20, choices=Method.choices,
                               blank=True, default="")
    transaction_id = models.CharField(max_length=120, blank=True, default="")
    paid_at = models.DateField(null=True, blank=True,
                                help_text="Date the customer says they paid.")
    payer_note = models.TextField(blank=True, default="")

    proof = models.FileField(upload_to=payment_proof_path, max_length=255,
                              null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices,
                               default=Status.AWAITING_PROOF, db_index=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    # Set when a reviewer claims the payment, so two platform admins cannot
    # review the same one simultaneously and reach different conclusions.
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                  null=True, blank=True,
                                  related_name="payments_claimed")
    review_started_at = models.DateTimeField(null=True, blank=True)

    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                     on_delete=models.PROTECT, null=True,
                                     blank=True, related_name="payments_verified")
    verified_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    # What the reviewer asked the customer for (NEEDS_INFO). Kept apart from
    # the rejection reason: a question is not a refusal, and the customer's
    # page words them differently.
    review_message = models.TextField(blank=True, default="")
    # WHO SENT THE RECEIPT, as text. The console reads payments in platform
    # scope, where row-level security (rightly) hides a tenant's User rows --
    # so `created_by.email` came back empty in the Payment Center under the
    # app role, while it worked in every test run without RLS. Copied at
    # submission, it is the one fact about the customer's person the platform
    # needs to show, and nothing more.
    submitted_by_email = models.EmailField(blank=True, default="")

    # Reserved for a future gateway (eSewa / Khalti / bank API). UNUSED in V1 --
    # present so integrating one is additive rather than a migration.
    gateway = models.CharField(max_length=30, blank=True, default="")
    gateway_ref = models.CharField(max_length=120, blank=True, default="")
    gateway_payload = models.JSONField(null=True, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                    on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name="payments_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "payment_reference"],
                                     name="uniq_payment_ref_per_org"),
            # One bank/eSewa transaction cannot be claimed twice by the same
            # tenant. Conditional, because the id is blank until proof is
            # submitted and a plain unique would allow only one pending payment.
            models.UniqueConstraint(
                models.functions.Lower("transaction_id"), "organization",
                condition=~models.Q(transaction_id=""),
                name="uniq_payment_txn_per_org"),
        ]
        indexes = [
            # The platform verification queue: "everything waiting on me".
            models.Index(fields=["status", "created_at"],
                         name="payment_queue_idx"),
        ]

    def __str__(self):
        return f"{self.payment_reference} ({self.organization.slug}, {self.status})"

    @property
    def amount_major(self):
        """For display only. Never use this for arithmetic."""
        from decimal import Decimal

        return Decimal(self.amount_minor) / Decimal(100)

    @property
    def is_open(self):
        return self.status in (self.Status.AWAITING_PROOF, self.Status.SUBMITTED,
                               self.Status.UNDER_REVIEW)


# ---------------------------------------------------------------------------
# Platform audit trail
# ---------------------------------------------------------------------------
class PlatformAuditLog(models.Model):
    """Append-only record of every action a platform operator takes ON a tenant.

    WHY THIS IS NOT ``audit.AuditLog``
    ----------------------------------
    ``audit.AuditLog`` is a TENANT table: Phase S5 gave it an
    ``organization`` column and a row-level-security policy, so a tenant's
    administrators can read their own trail. That is right for "HR approved
    this leave" and wrong for "the platform suspended this customer for
    non-payment", for three reasons:

      1. **Survivability.** The platform's record of what it did to a tenant
         must outlive the tenant. Cancelling a customer and purging their data
         must not take the evidence of the cancellation with it, so
         ``organization`` is nullable with ``SET_NULL``.

      2. **Readability.** A suspended tenant has no application access at all,
         which means a tenant-scoped trail of its own suspension is written to
         a table nobody can then read. This one is readable by the console
         that wrote it.

      3. **Visibility to the subject.** Internal notes on a billing dispute
         are not customer-facing records.

    It is therefore registered in ``tenancy.inventory.PLATFORM_GLOBAL`` and
    carries NO RLS policy -- deliberately, and asserted by a test. Nothing a
    tenant request can reach queries it: ``tenancy.platform_audit.record()``
    is the only writer and the platform console the only reader.

    APPEND-ONLY, ENFORCED. ``save()`` refuses to update an existing row and
    ``delete()`` refuses outright. An audit trail somebody can edit is a
    narrative, not evidence.
    """

    class Action(models.TextChoices):
        TENANT_CREATED = "tenant_created", "Organization created"
        TENANT_UPDATED = "tenant_updated", "Organization details changed"
        TENANT_BOOTSTRAPPED = "tenant_bootstrapped", "Workspace set up"
        TENANT_SUSPENDED = "tenant_suspended", "Organization suspended"
        TENANT_ACTIVATED = "tenant_activated", "Organization reactivated"
        TENANT_CANCELLED = "tenant_cancelled", "Organization cancelled"
        SUBSCRIPTION_CHANGED = "subscription_changed", "Subscription changed"
        PLAN_CHANGED = "plan_changed", "Plan changed"
        SUBSCRIPTION_EXTENDED = "subscription_extended", "Subscription extended"
        TRIAL_STARTED = "trial_started", "Trial started"
        BRANDING_CHANGED = "branding_changed", "Branding changed"
        SETTINGS_CHANGED = "settings_changed", "Settings changed"
        PAYMENT_VERIFIED = "payment_verified", "Payment verified"
        PAYMENT_REJECTED = "payment_rejected", "Payment rejected"
        # --- Phase S8: what the CUSTOMER does ---
        #
        # Recorded on the platform trail, not the tenant's own audit log,
        # even though a customer performed them. The trail's subject is the
        # relationship between the platform and that organization -- an
        # operator asking "why is this tenant active" needs the request, the
        # submission and the verification in one place, and the first two
        # would otherwise be inside the customer's workspace where the
        # console cannot read them under RLS.
        PAYMENT_REQUESTED = "payment_requested", "Plan requested by customer"
        PAYMENT_SUBMITTED = "payment_submitted", "Payment proof submitted"
        # --- Phase S9: branding and custom domains ---
        BRANDING_UPDATED = "branding_updated", "Branding updated by customer"
        DOMAIN_CLAIMED = "domain_claimed", "Custom domain claimed"
        DOMAIN_VERIFIED = "domain_verified", "Custom domain verified"
        DOMAIN_REMOVED = "domain_removed", "Custom domain removed"
        ADMIN_USER_CREATED = "admin_user_created", "First administrator created"
        # --- Phase S6.5: portability and lifecycle ---
        EXPORT_CREATED = "export_created", "Export created"
        EXPORT_DOWNLOADED = "export_downloaded", "Export downloaded"
        TENANT_ARCHIVED = "tenant_archived", "Organization archived"
        TENANT_RESTORED = "tenant_restored", "Organization restored"
        # Any retention decision taken against a tenant's data: an export
        # expiring and being discarded, a recovery window elapsing. Recorded
        # even when the action is "nothing was deleted", because a retention
        # policy nobody can evidence is not a policy.
        RETENTION_ACTION = "retention_action", "Retention action"
        # --- Client handover ---
        # The moment a new customer is given the way in, and the moment they
        # use it. Together they answer the support call that matters most in
        # a customer's first week: "did they ever get in?"
        PAYMENT_INFO_REQUESTED = "payment_info_requested", "More information requested on a payment"
        PAYMENT_METHOD_CHANGED = "payment_method_changed", "Payment method changed"
        ACCESS_SENT = "access_sent", "Sign-in details emailed"
        ACCESS_REISSUED = "access_reissued", "Temporary password reissued"
        ADMIN_FIRST_SIGN_IN = "admin_first_sign_in", "Client administrator signed in"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # SET_NULL, not CASCADE: see reason 1 in the class docstring.
    organization = models.ForeignKey(
        Organization, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="platform_audit_entries")
    # Denormalised so the entry still names its subject after the FK is nulled.
    organization_slug = models.CharField(max_length=63, blank=True, default="")
    organization_name = models.CharField(max_length=200, blank=True, default="")

    action = models.CharField(max_length=40, choices=Action.choices, db_index=True)

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True,
                               related_name="platform_audit_entries")
    actor_email = models.CharField(max_length=254, blank=True, default="")

    # What changed. Free-form by design: the shape differs per action, and a
    # column per fact would mean a migration every time the console grows a
    # button.
    changes = models.JSONField(null=True, blank=True)
    note = models.TextField(blank=True, default="")

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"],
                         name="platform_audit_org_idx"),
        ]

    def __str__(self):
        who = self.actor_email or "system"
        return f"{self.action} on {self.organization_slug or '-'} by {who}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise ValueError(
                "PlatformAuditLog is append-only; an entry cannot be edited. "
                "Record a new entry describing the correction instead.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "PlatformAuditLog is append-only; an entry cannot be deleted.")


# ---------------------------------------------------------------------------
# Phase S6.5: tenant data portability
# ---------------------------------------------------------------------------
class TenantExport(models.Model):
    """One export of one tenant's data: the request, the bundle, the receipt.

    PLATFORM-GLOBAL, NOT TENANT DATA, and the distinction is the same one
    ``PlatformAuditLog`` makes: this is the platform's record of what it did
    FOR a customer. A tenant must not be able to read it, list it or discover
    that an export exists, because an export is an operator action and its
    bundle is the entire workspace in one file.

    WHY THE MANIFEST IS STORED HERE AS WELL AS INSIDE THE ZIP. The copy inside
    the bundle is what the customer receives and verifies against; the copy in
    this row is what the platform can still answer from after the bundle has
    been handed over, expired or discarded -- "what did we give them, when,
    how many rows, and did it verify". A receipt that only exists inside the
    thing it describes is not a receipt.

    STATUS IS NOT DECORATION EITHER. Part 6 of the brief forbids partial
    exports, so a bundle becomes downloadable at exactly one moment: when
    every table has been written AND the integrity pass found no dangling
    reference. Anything else lands in FAILED with the reason, carrying no
    file -- an operator must never be able to hand a customer a half export
    without knowing it.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        # The bundle has been discarded under the retention policy; the row
        # stays, because the fact that an export happened is itself a record.
        EXPIRED = "expired", "Expired"

    class Contents(models.TextChoices):
        """What goes in the bundle. The ZIP is always a ZIP.

        JSON is the restorable form (Django's own serialisation, so ids and
        foreign keys survive); CSV is the readable form a customer's finance
        or HR team can open. FULL is both plus the media tree, which is what
        "customer exit" actually requires -- a dataset whose document rows
        point at files nobody shipped is not portable.
        """
        JSON = "json", "JSON only"
        CSV = "csv", "CSV only"
        FULL = "full", "JSON, CSV and media"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="exports")
    # Denormalised so a receipt still names its subject if the tenant row is
    # ever removed under the retention policy.
    organization_slug = models.CharField(max_length=63, blank=True, default="")

    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    contents = models.CharField(max_length=10, choices=Contents.choices,
                                default=Contents.FULL)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="tenant_exports_requested")
    requested_by_email = models.CharField(max_length=254, blank=True,
                                          default="")

    file = models.FileField(upload_to=export_bundle_path, max_length=255,
                            blank=True, null=True)
    size_bytes = models.BigIntegerField(default=0)
    # The checksum of the BUNDLE, so a file handed over can be shown to be
    # the file this row describes.
    sha256 = models.CharField(max_length=64, blank=True, default="")

    manifest = models.JSONField(null=True, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    media_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Retention: when the bundle may be discarded. The ROW is kept.
    expires_at = models.DateTimeField(null=True, blank=True)

    downloaded_at = models.DateTimeField(null=True, blank=True)
    download_count = models.PositiveIntegerField(default=0)
    last_downloaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="tenant_exports_downloaded")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"],
                         name="tenant_export_org_idx"),
        ]

    def __str__(self):
        return f"export of {self.organization_slug or '-'} ({self.status})"

    @property
    def is_downloadable(self):
        return bool(self.status == self.Status.READY and self.file)


# ---------------------------------------------------------------------------
# Phase S7: public self-service registration
# ---------------------------------------------------------------------------
class PendingRegistration(models.Model):
    """Somebody has asked for a workspace. No tenant exists yet.

    WHY THIS TABLE EXISTS AT ALL. Part 3 of the brief is explicit: "No tenant
    created until verification." That single sentence rules out the obvious
    implementation -- provision on submit, activate on click -- and forces the
    registration to be a record of its own. It is the right rule and not only
    for tidiness: an unverified registration can be submitted by anybody about
    anybody, so provisioning first would let a stranger take `nif`, consume
    the slug, appear on the platform dashboard as a customer, and send mail
    from an address they do not control.

    So this row is everything needed to build a tenant later, and nothing a
    tenant needs now.

    THE PASSWORD IS STORED HASHED, BEFORE ANY USER EXISTS.
    The registrant chooses their password on the form (Part 1), and the
    account it belongs to is not created until they click the link in their
    email. Something has to hold it in between. It is put through
    ``make_password`` immediately and the plaintext is never written anywhere
    -- not to this row, not to a log, not to the audit trail -- and
    provisioning installs the hash directly on the new user. The registrant
    therefore signs in with the password they chose, and the platform never
    knew it.

    THE TOKEN IS STORED HASHED TOO, for the same reason a password reset token
    is: this table is readable by anybody who can read the database, and a
    token in it is a live credential for creating a tenant and an admin
    account. The emailed link carries the only copy.

    THE SLUG IS RESERVED WHILE PENDING. Two people registering `hospital`
    within the same hour must not both be told it is theirs, and the first to
    verify must not fail because the second verified first. A partial unique
    index over the live statuses holds the reservation; `EXPIRED` and
    `CANCELLED` rows release it.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting email verification"
        VERIFIED = "verified", "Email verified"
        PROVISIONED = "provisioned", "Workspace created"
        EXPIRED = "expired", "Expired unverified"
        # Kept rather than deleted: a cancelled registration is evidence about
        # an address that asked for a workspace, which matters if the same
        # address is later used for abuse.
        CANCELLED = "cancelled", "Cancelled"

    # The statuses that hold a slug reservation.
    LIVE_STATUSES = ("pending", "verified")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization_name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=63, validators=[validate_tenant_slug])
    industry = models.CharField(max_length=100, blank=True, default="")
    country = models.CharField(max_length=2, blank=True, default="")
    organization_email = models.EmailField()

    admin_name = models.CharField(max_length=150, blank=True, default="")
    admin_email = models.EmailField(db_index=True)
    # Hashed on arrival; see the class docstring.
    admin_password = models.CharField(max_length=256, editable=False)

    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    # SHA-256 of the emailed token, never the token.
    token_hash = models.CharField(max_length=64, db_index=True, editable=False)
    token_expires_at = models.DateTimeField()

    # Set when provisioning succeeds, so a verified registration can be
    # traced to the workspace it became -- and so a second click on the same
    # link is answered with "already done" rather than a second tenant.
    organization = models.ForeignKey(
        Organization, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="registration")

    # Abuse forensics. Recorded because a registration is the one thing an
    # anonymous caller can do that costs the platform a whole tenant.
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default="")
    verification_sends = models.PositiveIntegerField(default=0)
    verification_attempts = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    provisioned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # The reservation. Partial, so an expired or cancelled attempt
            # releases the subdomain for somebody else.
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(status__in=["pending", "verified"]),
                name="uniq_pending_registration_slug"),
            # And one live registration per administrator address, so a
            # refresh of the form does not queue a second workspace.
            models.UniqueConstraint(
                fields=["admin_email"],
                condition=models.Q(status__in=["pending", "verified"]),
                name="uniq_pending_registration_admin_email"),
        ]
        indexes = [
            models.Index(fields=["status", "-created_at"],
                         name="pending_reg_status_idx"),
        ]

    def __str__(self):
        return f"registration for {self.slug} ({self.status})"

    @property
    def is_expired(self):
        return timezone.now() >= self.token_expires_at

    @property
    def is_live(self):
        return self.status in self.LIVE_STATUSES


class PlatformMetric(models.Model):
    """A daily counter the PLATFORM may read. Phase S6.75 Part 4.

    WHY THIS TABLE HAS TO EXIST, AND WHY IT IS SO SMALL.
    The brief asks for dashboards of tenant creation, provisioning outcomes,
    logins, subscriptions, archives and exports. Six of those eight are
    already answerable: ``PlatformAuditLog`` records them and is
    platform-global, so the console can count them with no privilege over
    customer data.

    Logins are the exception. A successful or failed sign-in is written to
    ``audit.AuditLog``, which is TENANT-scoped and carries a row-level
    security policy -- so a console query with no tenant bound returns zero,
    for every tenant, silently. That is the same wall the Phase S6 dashboard
    met over headcount, and the answer here is the same one: a maintained
    counter on a platform table, rather than giving the console a way to read
    tenant rows.

    WHAT IS DELIBERATELY NOT STORED: who signed in, from where, or into what.
    A count per tenant per day per outcome, and nothing else. The identifying
    detail already lives in the tenant's own audit trail, where that tenant's
    administrators can see it and the platform cannot -- and moving it here to
    draw a nicer graph would quietly undo that.
    """

    class Key(models.TextChoices):
        LOGIN_OK = "login_ok", "Login succeeded"
        LOGIN_FAILED = "login_failed", "Login failed"
        PROVISION_OK = "provision_ok", "Provisioning succeeded"
        PROVISION_FAILED = "provision_failed", "Provisioning failed"
        EMAIL_SENT = "email_sent", "Platform email sent"
        EMAIL_FAILED = "email_failed", "Platform email failed"
        # Adoption (customer success). Counts of USE, per organization per
        # day -- never who, never what. They let the console say whether a
        # customer is actually using the product without the console being
        # able to read a single record of theirs.
        ACTIVE_USER = "active_user", "Active user (once per person per day)"
        ATTENDANCE_USED = "attendance_used", "Attendance recorded"
        LEAVE_USED = "leave_used", "Leave applied for"
        TASK_USED = "task_used", "Task created"
        DOCUMENT_USED = "document_used", "Document created"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    day = models.DateField(db_index=True)
    key = models.CharField(max_length=32, choices=Key.choices, db_index=True)
    # Nullable: a failed sign-in for an address belonging to nobody has no
    # tenant, and inventing one would hide the probe -- the same reasoning as
    # audit.AuditLog's nullable organization.
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True,
        related_name="metrics")
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["day", "key", "organization"],
                                    name="uniq_platform_metric_day_key_org"),
        ]
        indexes = [
            models.Index(fields=["key", "-day"], name="platform_metric_idx"),
        ]
        ordering = ["-day", "key"]

    def __str__(self):
        return f"{self.day} {self.key} = {self.count}"


class TenantDomain(models.Model):
    """Phase S9: a hostname a customer owns, pointed at their workspace.

    WHY THIS IS A TABLE AND NOT THE ``Organization.domain`` COLUMN IT
    REPLACES. That column accepted any hostname with a uniqueness check and
    the resolver honoured it immediately -- so "give us hr.abcschool.edu.np"
    was granted on the customer's word, and nothing stopped a typo, a
    mistake or a malicious request from pointing a competitor's hostname (or
    a bank's) at a tenant. A hostname is a claim about something outside this
    platform, and the only way to settle it is to ask DNS.

    So a domain now has a LIFECYCLE, and the resolver honours exactly one
    state of it:

        pending  -- claimed, token issued, DNS not yet proving anything
        verified -- the record was found; the customer owns the name
        active   -- verified AND serving (what the resolver matches)
        failed   -- checked and the record was absent or wrong
        removed  -- withdrawn; kept as a record that it was once claimed

    PLATFORM-GLOBAL, like every other row that maps a hostname to a tenant:
    the resolver reads it on every request with no tenant bound, which is
    only possible for a table outside the row-level security policies.

    ``Organization.domain`` is left in place and still read by the resolver
    for compatibility, but it is no longer the way a domain is added -- see
    ``tenancy.domains`` for why removing it outright would break every
    deployment that already has one set.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting DNS verification"
        VERIFIED = "verified", "Ownership verified"
        ACTIVE = "active", "Active"
        FAILED = "failed", "Verification failed"
        REMOVED = "removed", "Removed"

    class Method(models.TextChoices):
        TXT = "txt", "TXT record"
        CNAME = "cname", "CNAME record"

    # The states in which the resolver will serve this hostname. A pending or
    # failed domain resolves to nothing, which is the whole point.
    SERVING_STATUSES = ("active",)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="domains")

    # 253 is the maximum length of a DNS name. Stored lowercase; the
    # uniqueness is global because a hostname can only point at one tenant.
    hostname = models.CharField(max_length=253, unique=True, db_index=True)

    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    method = models.CharField(max_length=10, choices=Method.choices,
                              default=Method.TXT)
    # The value the customer must publish. Random per domain, so proving one
    # hostname proves nothing about another.
    verification_token = models.CharField(max_length=64, editable=False)

    # One domain per tenant is the one their own links are built from.
    is_primary = models.BooleanField(default=False)

    verified_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    # What DNS actually said last time, for a customer who cannot see why
    # their record is not being found.
    last_error = models.CharField(max_length=300, blank=True, default="")
    check_count = models.PositiveIntegerField(default=0)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="tenant_domains_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "hostname"]
        constraints = [
            # At most one primary per organization. Partial, so withdrawing a
            # primary frees the slot without having to clear the flag first.
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(is_primary=True),
                name="uniq_primary_domain_per_org"),
        ]
        indexes = [
            models.Index(fields=["status", "hostname"],
                         name="tenant_domain_status_idx"),
        ]

    def __str__(self):
        return f"{self.hostname} -> {self.organization_id} ({self.status})"

    @property
    def is_serving(self):
        return self.status in self.SERVING_STATUSES

    @property
    def expected_record_name(self):
        """Where the customer must publish the proof.

        A dedicated ``_nifn-verify`` label rather than the hostname itself:
        a TXT record on the hostname they intend to serve from would collide
        with SPF, DMARC and anything else already published there, and a
        customer should never have to choose between proving ownership to us
        and keeping their email working.
        """
        if self.method == self.Method.CNAME:
            return f"_nifn-verify.{self.hostname}"
        return f"_nifn-verify.{self.hostname}"

    def expected_record_value(self, *, platform_host=""):
        if self.method == self.Method.CNAME:
            return f"{self.verification_token}.{platform_host}".rstrip(".")
        return f"nifn-verify={self.verification_token}"



class SupportRequest(models.Model):
    """Contact support, report a problem, request a feature, give feedback.

    PLATFORM-GLOBAL, like Payment: it is the customer talking TO the
    platform, and the platform team has to read it from the console without
    any privilege over the customer's records. So it carries copies of the
    few facts the platform needs -- who (email and name), where (the page),
    what -- rather than references the console could not follow under
    row-level security.
    """

    class Kind(models.TextChoices):
        CONTACT = "contact", "Question"
        PROBLEM = "problem", "Problem report"
        FEATURE = "feature", "Feature request"
        FEEDBACK = "feedback", "Feedback"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True,
        related_name="support_requests")
    kind = models.CharField(max_length=20, choices=Kind.choices, db_index=True)
    subject = models.CharField(max_length=200, blank=True, default="")
    message = models.TextField(blank=True, default="")
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    feature = models.CharField(max_length=80, blank=True, default="")
    page = models.CharField(max_length=300, blank=True, default="")
    user_agent = models.CharField(max_length=300, blank=True, default="")
    submitted_by_email = models.EmailField(blank=True, default="")
    submitted_by_name = models.CharField(max_length=150, blank=True, default="")
    submitted_by_role = models.CharField(max_length=30, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.OPEN, db_index=True)
    response = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.subject or self.feature}"
