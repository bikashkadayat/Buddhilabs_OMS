import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from tenancy.scoping import AllTenantsManager, TenantManager

class LeaveBalance(models.Model):
    """
    Tracks the total and used leave balances per user per year.
    """
    class LeaveType(models.TextChoices):
        ANNUAL = "annual", "Annual Leave"
        SICK = "sick", "Sick Leave"
        CASUAL = "casual", "Casual Leave"
        # Category engine leave types (entitlements live in EntitlementRule).
        MATERNITY = "maternity", "Maternity Leave"
        PATERNITY = "paternity", "Paternity Leave"
        COMPENSATORY = "compensatory", "Compensatory Leave"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="leave_balances")
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    year = models.IntegerField()
    total_allocated = models.IntegerField()
    used_so_far = models.FloatField(default=0)

    class Meta:
        unique_together = ('user', 'leave_type', 'year')

    @property
    def remaining(self):
        # Never surface a negative balance (e.g. if an entitlement is cut below a
        # user's already-approved usage): clamp at 0. `used_so_far` stays truthful.
        return max(0, self.total_allocated - self.used_so_far)

    def __str__(self):
        return f"{self.user} - {self.leave_type} ({self.year})"


class Leave(models.Model):
    """
    Leave Application tracking model.
    """
    class Status(models.TextChoices):
        # Two-stage flow (Phase 2.6): PENDING = awaiting Department Head (L1);
        # PENDING_HR = Dept Head approved, awaiting HR (L2); then APPROVED/REJECTED.
        # Existing rows keep PENDING/APPROVED/REJECTED - fully backward compatible.
        PENDING = "pending", "Pending (Department Head)"
        PENDING_HR = "pending_hr", "Pending (HR)"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    # Duration of each leave day. Values mirror LeaveDayRecord.DayPortion so the
    # stored string can be passed straight into day-record generation; a half-day
    # portion makes every generated day weigh 0.5 in the balance engine.
    class DayPortion(models.TextChoices):
        FULL = "full", "Full Day"
        FIRST_HALF = "first_half", "First Half"
        SECOND_HALF = "second_half", "Second Half"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="leaves_applied")
    leave_type = models.CharField(max_length=20, choices=LeaveBalance.LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    handover_notes = models.TextField(blank=True, default='')
    # Full day, or a first-/second-half day. Half-day is only meaningful for a
    # single-date application (enforced in the serializer).
    day_portion = models.CharField(
        max_length=20, choices=DayPortion.choices, default=DayPortion.FULL)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Final approver (HR). Kept for backward compatibility - still set on final action.
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="leaves_to_approve")

    # Phase 2.6: two-stage review trail (all nullable/additive).
    department_head_reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="leaves_dh_reviewed",
    )
    department_head_action_date = models.DateTimeField(null=True, blank=True)
    hr_reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="leaves_hr_reviewed",
    )
    hr_action_date = models.DateTimeField(null=True, blank=True)
    # Phase BOD-ROLE-EXECUTIVE-GOVERNANCE: a Department Head's (or a Board
    # member's) leave is decided by the Board, not by a department head. Its own
    # fields, so the trail never records a Board member as a "Department Head".
    board_reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="leaves_board_reviewed",
    )
    board_action_date = models.DateTimeField(null=True, blank=True)
    # Reviewer remarks (rejection reason / approval note), shown to the employee.
    remarks = models.TextField(blank=True, default='')

    # TODO(deferred, Phase 5 candidate): add a `rejection_comment` field so an
    # approver's reason is stored and shown to the maker. The current model has
    # no way to capture why a leave was rejected (set_status only flips status).

    # Phase 5: soft-delete. Leaves with approved day records are never hard
    # deleted (integrity); they are flagged here instead.
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} - {self.leave_type} ({self.status})"


# ===========================================================================
# Phase 4 - Enterprise Leave Records
#
# These models EXTEND the Level 1 leaves app; the original Leave / LeaveBalance
# above are intentionally left untouched. New enterprise fields live on new
# tables so existing endpoints keep working.
# ===========================================================================

# Roles are duplicated as plain choices here (rather than importing users.User)
# to avoid an import cycle at module load; they mirror users.User.Roles.
ROLE_CHOICES = [
    ("maker", "Maker"),
    ("checker", "Checker"),
    ("approver", "Approver"),
    ("admin", "Admin"),
]


class Department(models.Model):
    """Organizational unit, optionally nested (parent) for reporting hierarchy."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=30)
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    head = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="departments_headed",
    )
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="children",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            # Was unique=True on `code` alone, which meant two tenants could
            # never both have an "HR" department.
            models.UniqueConstraint(fields=["organization", "code"],
                                    name="uniq_department_org_code"),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


class LeaveType(models.Model):
    """
    CMS-driven leave type. Replaces the hardcoded LeaveBalance.LeaveType enum
    for all Phase 4 features; admins manage these from the dashboard.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=20)
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    name = models.CharField(max_length=100)
    default_days_per_year = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    is_paid = models.BooleanField(default=True)
    allow_half_day = models.BooleanField(default=True)
    allow_carry_forward = models.BooleanField(default=False)
    max_carry_forward_days = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    requires_document = models.BooleanField(default=False)
    min_notice_days = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    display_color = models.CharField(max_length=7, default="#6B7280")  # hex for calendar UI
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            # Was unique=True on `code`: two tenants could never both have an
            # "ANNUAL" leave type.
            models.UniqueConstraint(fields=["organization", "code"],
                                    name="uniq_leavetype_org_code"),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


class LeavePolicy(models.Model):
    """
    Overrides a LeaveType's default entitlement for a department and/or role,
    effective over a date range. Null department => org-wide; null role => all
    roles. Resolution prefers the most specific match (see services).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name="policies")
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, null=True, blank=True, related_name="leave_policies",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, null=True, blank=True)
    days_per_year = models.DecimalField(max_digits=6, decimal_places=2)
    effective_from = models.DateField()
    effective_until = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="leave_policies_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["leave_type", "department", "role", "effective_from"],
                name="uniq_policy_scope_effective",
            ),
        ]

    def __str__(self):
        scope = self.department.code if self.department else "ORG"
        return f"{self.leave_type.code}/{scope}/{self.role or 'ALL'} = {self.days_per_year}d"


class Holiday(models.Model):
    """CMS-driven public holiday; excluded from working-day calculations."""
    class HolidayType(models.TextChoices):
        PUBLIC = "public", "Public"
        OPTIONAL = "optional", "Optional"
        RELIGIOUS = "religious", "Religious"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField()
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    name = models.CharField(max_length=150)
    holiday_type = models.CharField(max_length=20, choices=HolidayType.choices, default=HolidayType.PUBLIC)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["date"]
        constraints = [
            # Was unique=True on `date`, which meant two tenants could never
            # disagree about whether a given day was a holiday.
            models.UniqueConstraint(fields=["organization", "date"],
                                    name="uniq_holiday_org_date"),
        ]

    def __str__(self):
        return f"{self.date} - {self.name}"


class CalendarEvent(models.Model):
    """
    A dated entry shown on the Nepali calendar: a festival, an observance, a
    national day, a jayanti.

    WHY THIS IS NOT `Holiday`
    -------------------------
    `Holiday` is the leave engine's table. Its `date` is UNIQUE and every row in
    it is excluded from working-day calculations, so it can hold exactly one
    entry per day and every entry it holds is a day off. A Nepali calendar is
    neither: Hamro Patro shows several entries on a busy day (a festival plus a
    jayanti plus an observance), and most of what it shows is NOT a public
    holiday — Ghatasthapana is marked, but the office is open.

    Putting festivals into `Holiday` would therefore have turned every marked
    day into a non-working day and silently inflated everyone's leave balance.
    This table is display-only and touches no calculation; the two are joined
    on the date when the calendar is drawn.

    `name_np` is separate rather than the only name because the product is
    bilingual: the calendar shows Devanagari, while exports, emails and the
    audit trail are read in English by people who do not read Nepali.
    """
    class EventType(models.TextChoices):
        FESTIVAL = "festival", "Festival"
        NATIONAL = "national", "National day"
        RELIGIOUS = "religious", "Religious observance"
        JAYANTI = "jayanti", "Jayanti"
        OBSERVANCE = "observance", "Observance"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    # NOT unique: a single day can carry several entries, which is the whole
    # reason this table exists.
    date = models.DateField(db_index=True)
    name = models.CharField(max_length=150)
    name_np = models.CharField(
        max_length=150, blank=True, default="",
        help_text="Devanagari name, shown on the calendar. Falls back to `name`.")
    event_type = models.CharField(
        max_length=20, choices=EventType.choices, default=EventType.FESTIVAL)
    # Tithi is the lunar day ("प्रतिपदा"). Optional and free text: it is supplied
    # with the data, never computed here — panchanga arithmetic done wrong puts
    # a festival on the wrong day, and a wrong festival date in an HR system
    # means somebody works a public holiday.
    tithi = models.CharField(max_length=80, blank=True, default="")
    detail = models.TextField(blank=True, default="")
    # Marks an entry the organisation treats as non-working. The authority for
    # that remains the Holiday table; this only lets the calendar colour it.
    is_public_holiday = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["date", "-is_public_holiday", "name"]
        indexes = [models.Index(fields=["date", "is_active"])]
        constraints = [
            # Was (date, name) with no tenant column at all: two companies
            # could not both record "Dashain" on the same date.
            models.UniqueConstraint(fields=["organization", "date", "name"],
                                    name="uniq_calendar_event_org_date_name"),
        ]

    def __str__(self):
        return f"{self.date} - {self.name_np or self.name}"

    @property
    def display_name(self):
        return self.name_np or self.name


class LeaveDayRecord(models.Model):
    """
    ATOMIC per-day source of truth. One row per calendar day covered by a leave
    request. Every weekly/monthly summary and balance is derived from this
    table, which makes reporting fast and fully auditable.
    """
    class DayPortion(models.TextChoices):
        FULL = "full", "Full Day"
        FIRST_HALF = "first_half", "First Half"
        SECOND_HALF = "second_half", "Second Half"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    PORTION_WEIGHTS = {
        DayPortion.FULL: Decimal("1.0"),
        DayPortion.FIRST_HALF: Decimal("0.5"),
        DayPortion.SECOND_HALF: Decimal("0.5"),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    leave_request = models.ForeignKey(Leave, on_delete=models.CASCADE, related_name="day_records")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="leave_day_records")
    date = models.DateField()
    day_portion = models.CharField(max_length=20, choices=DayPortion.choices, default=DayPortion.FULL)
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="day_records")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    is_holiday = models.BooleanField(default=False)
    is_weekend = models.BooleanField(default=False)
    week_number = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    year = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date"]
        indexes = [
            models.Index(fields=["user", "year", "month"]),
            models.Index(fields=["user", "year", "week_number"]),
            models.Index(fields=["date", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "date", "day_portion", "leave_request"],
                name="uniq_leave_day_booking",
            ),
        ]

    @property
    def portion_days(self):
        """Decimal weight this record contributes to a day count (1.0 or 0.5)."""
        return self.PORTION_WEIGHTS.get(self.day_portion, Decimal("1.0"))

    @property
    def is_working_day(self):
        return not (self.is_holiday or self.is_weekend)

    def __str__(self):
        return f"{self.user} {self.date} {self.day_portion} ({self.status})"


class EnterpriseLeaveBalance(models.Model):
    """
    Phase 4 balance, recomputed idempotently from LeaveDayRecord + LeavePolicy.
    (The Level 1 LeaveBalance above is kept for backward compatibility.)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enterprise_balances")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name="balances")
    year = models.PositiveIntegerField()
    entitled_days = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    carried_forward_days = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    used_days = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    pending_days = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    encashed_days = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    forfeited_days = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    # Phase 7: manual HR adjustment (bonus/deduction). Preserved by recompute
    # (never derived from records); every change is audit-logged with a reason.
    adjustment_days = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    last_recomputed_at = models.DateTimeField(null=True, blank=True)
    # Phase 5: set by process_year_end to freeze a closed year's balance.
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "leave_type"]
        constraints = [
            models.UniqueConstraint(fields=["user", "leave_type", "year"], name="uniq_enterprise_balance"),
        ]

    @property
    def available_days(self):
        """entitled + carried_forward + adjustment - used - pending."""
        return (
            self.entitled_days + self.carried_forward_days + self.adjustment_days
            - self.used_days - self.pending_days
        )

    def __str__(self):
        return f"{self.user} {self.leave_type.code} {self.year}: {self.available_days} avail"


# ===========================================================================
# Experience-based entitlement engine (category-driven, DB source of truth).
# EntitlementRule replaces the flat LeaveType.default_days_per_year path for the
# simple dashboard balances; CompensatoryLedger tracks earned/used comp days.
# ===========================================================================

# Category values mirror users.User.LeaveCategory (duplicated as plain choices to
# avoid an import cycle at model-load; the engine maps between them).
LEAVE_CATEGORY_CHOICES = [
    ("A", "Category A — Permanent (>3 yrs)"),
    ("B", "Category B — Permanent (1–3 yrs)"),
    ("C", "Category C — Post-Probation / Permanent (<1 yr)"),
    ("D", "Category D — Intern"),
    ("E", "Category E — Volunteer"),
    ("PROBATION", "Probation (<3 mo)"),
]


class EntitlementRule(models.Model):
    """
    HR/Admin-configurable entitlement: how many days of a leave type a given
    category receives. This is the SOURCE OF TRUTH for yearly allocations
    (seeded with the A/B/C/D/PROBATION matrix, editable from the admin CMS).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    category = models.CharField(max_length=12, choices=LEAVE_CATEGORY_CHOICES)
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name="entitlement_rules")
    entitlement_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    # Annual/Sick are counted in working days (Sat + holidays excluded); fixed
    # entitlements like Maternity/Paternity are calendar-day based.
    is_working_day_based = models.BooleanField(default=True)
    # False => this leave type is not offered to this category (e.g. Maternity for
    # Interns). Compensatory rows are applicable=True but entitlement_days=0
    # because comp is earned into the ledger, not allocated yearly.
    applicable = models.BooleanField(default=True)
    # A THIRD STATE, between "N days a year" and "not offered at all".
    #
    # The policy says an intern's maternity and paternity leave is granted "as
    # per organization policy" — real leave, decided case by case, with no
    # fixed annual allocation. Both existing fields lie about that: a 0-day
    # allocation reads as "you get none", and applicable=False hides it
    # entirely. Either way an intern is told something untrue.
    #
    # When this is set the leave type is SHOWN with no numeric balance and a
    # "by arrangement" note, and no LeaveBalance row is generated (there is no
    # number to hold). `applicable` still governs whether it is offered at all.
    by_arrangement = models.BooleanField(
        default=False,
        help_text="Granted case-by-case under organisation policy rather than "
                  "as a fixed yearly allocation.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "leave_type__code"]
        constraints = [
            # `category` is a bare string enum, so this tuple had no
            # tenant-owned column except leave_type. This table encodes ONE
            # organisation's leave policy; it is the most policy-specific
            # table in the system and gets the column explicitly.
            models.UniqueConstraint(fields=["organization", "category", "leave_type"],
                                    name="uniq_entitlement_org_category_type"),
        ]

    def __str__(self):
        return f"{self.category}/{self.leave_type.code} = {self.entitlement_days}"


class CompensatoryLedger(models.Model):
    """
    Append-only ledger of compensatory (comp) leave. Comp is EARNED (working a
    weekend/holiday), not allocated yearly. Available = confirmed earned - used.

    Earn sources:
      * ATTENDANCE - auto-created (status=PENDING) when a check-in lands on a
        Saturday/public holiday; a manager/HR must CONFIRM before it counts.
      * HR_GRANT   - manual HR entry for off-system/field work; CONFIRMED at once.
    Use entries are created when comp leave is approved (status=CONFIRMED).
    """
    class EntryType(models.TextChoices):
        EARN = "earn", "Earned"
        USE = "use", "Used"

    class Source(models.TextChoices):
        ATTENDANCE = "attendance", "Attendance (weekend/holiday work)"
        HR_GRANT = "hr_grant", "HR manual grant"
        LEAVE_USE = "leave_use", "Comp leave taken"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending confirmation"
        CONFIRMED = "confirmed", "Confirmed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comp_ledger")
    entry_type = models.CharField(max_length=8, choices=EntryType.choices)
    days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("1.00"))
    source = models.CharField(max_length=12, choices=Source.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    source_date = models.DateField(null=True, blank=True, help_text="Weekend/holiday worked (for earn entries).")
    leave = models.ForeignKey(Leave, on_delete=models.SET_NULL, null=True, blank=True, related_name="comp_entries")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="comp_approved",
    )
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "entry_type", "status"])]
        constraints = [
            # Phase 8: attendance-sourced earns must be idempotent.
            # `rederive_attendance` re-runs derivation over arbitrary date
            # ranges after every mapping change; without this, each run would
            # mint another comp day for the same Saturday. Scoped to
            # source='attendance' so HR grants and comp usage are untouched.
            models.UniqueConstraint(
                fields=["user", "source_date", "entry_type"],
                condition=models.Q(source="attendance", source_date__isnull=False),
                name="uniq_attendance_comp_earn_per_day",
            ),
        ]

    def __str__(self):
        return f"{self.user} {self.entry_type} {self.days} ({self.status})"


class WeeklyLeaveSummary(models.Model):
    """Materialized weekly aggregate, recomputed from LeaveDayRecord."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="weekly_summaries")
    year = models.PositiveIntegerField()
    week_number = models.PositiveSmallIntegerField()
    week_start_date = models.DateField()
    week_end_date = models.DateField()
    total_leave_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    by_type = models.JSONField(default=dict, blank=True)
    approved_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    pending_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    rejected_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    working_days = models.PositiveSmallIntegerField(default=0)
    attendance_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("100.00"))
    last_recomputed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "-week_number"]
        constraints = [
            models.UniqueConstraint(fields=["user", "year", "week_number"], name="uniq_weekly_summary"),
        ]

    def __str__(self):
        return f"{self.user} {self.year}-W{self.week_number}"


class MonthlyLeaveSummary(models.Model):
    """Materialized monthly aggregate, recomputed from LeaveDayRecord."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="monthly_summaries")
    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()
    total_leave_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    by_type = models.JSONField(default=dict, blank=True)
    approved_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    pending_days = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    working_days = models.PositiveSmallIntegerField(default=0)
    attendance_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("100.00"))
    carry_forward_earned = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    last_recomputed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(fields=["user", "year", "month"], name="uniq_monthly_summary"),
        ]

    def __str__(self):
        return f"{self.user} {self.year}-{self.month:02d}"
