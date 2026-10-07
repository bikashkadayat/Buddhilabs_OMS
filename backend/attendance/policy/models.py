"""Policy engine tables.

These live in the ``attendance`` app (not a new one) because they *are*
attendance policy: ``Attendance`` holds foreign keys to them, and a separate app
would add a fifth migration graph plus a circular app dependency for no gain.
``biometric`` stayed separate because it owns genuinely different nouns.

Every default here reproduces the value the corresponding ``ATTENDANCE_*``
setting has today, so the seed migration is provably behaviour-preserving.
"""
import uuid
from datetime import time
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from tenancy.scoping import AllTenantsManager, TenantManager


class Shift(models.Model):
    """A working window. Overrides a policy's office start for timing only.

    A shift answers "when is this person expected?"; a policy answers "what do
    the hours mean?". Keeping them apart makes "move Ram to evenings" a
    one-row change instead of a duplicated policy.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=40)
    name = models.CharField(max_length=60)
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

    start_time = models.TimeField()
    end_time = models.TimeField()
    grace_minutes = models.PositiveSmallIntegerField(
        default=0, help_text="Minutes after start_time before a check-in counts as late.")
    break_minutes = models.PositiveSmallIntegerField(
        default=0, help_text="Unpaid break length. Only deducted when the policy says so.")
    # Ships now so the column never needs a second migration, but writes are
    # rejected: derivation buckets punches by local_date, so a 22:00->06:00
    # shift lands its two halves on two different Attendance rows and
    # unique_together (employee, date) leaves nowhere to merge them.
    crosses_midnight = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_time", "name"]
        constraints = [
            # Both `code` and `name` were unique=True standalone: two tenants
            # could never both have a "general" shift, nor both call one
            # "General Shift".
            models.UniqueConstraint(fields=["organization", "code"],
                                    name="uniq_shift_org_code"),
            models.UniqueConstraint(fields=["organization", "name"],
                                    name="uniq_shift_org_name"),
        ]

    def __str__(self):
        return f"{self.name} ({self.start_time:%H:%M}-{self.end_time:%H:%M})"

    def clean(self):
        if self.crosses_midnight:
            raise ValidationError({
                "crosses_midnight": "Overnight shifts are not supported yet.",
            })

    def save(self, *args, **kwargs):
        # Enforced here rather than only in clean() so no code path — admin,
        # API, shell, data migration — can create an unsupported shift.
        if self.crosses_midnight:
            raise ValidationError("Overnight shifts are not supported yet.")
        return super().save(*args, **kwargs)


class AttendancePolicy(models.Model):
    """A named bundle of attendance rules.

    Field-by-field origin of the defaults:
        office_start_time  <- ATTENDANCE_OFFICE_START    ("10:00")
        absent_cutoff_time <- ATTENDANCE_ABSENT_CUTOFF   ("18:00")
        half_day_hours     <- ATTENDANCE_HALF_DAY_HOURS  (5)
        full_day_hours     <- ATTENDANCE_FULL_DAY_HOURS  (8, dead until now)
        grace_minutes      =  0   (no grace period exists today)

    ``overtime_threshold_hours`` is NULL and ``comp_off_enabled`` is False by
    default so that migrating changes nothing. Each new behaviour is opt-in.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
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

    description = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)

    # --- timing -----------------------------------------------------------
    office_start_time = models.TimeField(default=time(10, 0))
    grace_minutes = models.PositiveSmallIntegerField(
        default=0,
        help_text="Legacy grace window. Only consulted when late_after_time is empty.")
    absent_cutoff_time = models.TimeField(
        default=time(18, 0),
        help_text="Local time after which today may be judged Absent.")

    # --- arrival-based status boundaries (Phase 8.1) ----------------------
    # These express the same idea as grace_minutes but as a wall-clock time an
    # admin can read. late_after_time wins when set; NULL selects the legacy
    # office_start_time + grace_minutes path, which stays fully supported.
    late_after_time = models.TimeField(
        null=True, blank=True, default=time(11, 45),
        help_text="Check-in at or before this is Present; after it is Late. "
                  "Leave empty to use office_start_time + grace_minutes instead.")
    half_day_after_time = models.TimeField(
        null=True, blank=True, default=time(13, 0),
        help_text="Check-in at or after this is Half Day regardless of hours "
                  "worked. Leave empty to disable the arrival-based half-day rule.")

    # --- hours ------------------------------------------------------------
    half_day_hours = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("5.00"))
    full_day_hours = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("8.00"))
    deduct_breaks = models.BooleanField(
        default=False,
        help_text="Subtract paired break punches from worked hours. The live "
                  "device has recorded zero break punches, so this is off.")

    # --- overtime ---------------------------------------------------------
    overtime_threshold_hours = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        help_text="Hours beyond which time counts as overtime. NULL disables overtime entirely.")
    overtime_min_minutes = models.PositiveSmallIntegerField(
        default=30, help_text="Ignore overtime shorter than this.")

    # --- compensatory off -------------------------------------------------
    comp_off_enabled = models.BooleanField(default=False)
    comp_off_on_saturday = models.BooleanField(default=True)
    comp_off_on_holiday = models.BooleanField(default=True)
    comp_off_min_hours = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("4.00"),
        help_text="Absolute floor: below this, nothing is earned.")
    comp_off_half_day_hours = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("4.00"),
        help_text="Worked hours earning 0.5 comp days.")
    comp_off_full_day_hours = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("6.00"),
        help_text="Worked hours earning 1.0 comp days.")

    default_shift = models.ForeignKey(
        Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name="policies")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attendance_policies_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "attendance policies"
        ordering = ["name"]
        constraints = [
            # Was unique=True on `name`: two tenants could not both have a
            # policy called "Default".
            models.UniqueConstraint(fields=["organization", "name"],
                                    name="uniq_attendance_policy_org_name"),
        ]

    def __str__(self):
        return self.name

    # A settings-derived fallback is built unsaved; nothing may persist it.
    is_fallback = False

    def clean(self):
        errors = {}
        if self.half_day_hours is not None and self.full_day_hours is not None:
            if self.half_day_hours >= self.full_day_hours:
                errors["half_day_hours"] = "Half-day hours must be less than full-day hours."
        if self.grace_minutes is not None and self.grace_minutes >= 480:
            errors["grace_minutes"] = "Grace period must be under 8 hours."
        if (self.comp_off_half_day_hours is not None
                and self.comp_off_full_day_hours is not None
                and self.comp_off_half_day_hours > self.comp_off_full_day_hours):
            errors["comp_off_half_day_hours"] = (
                "Half-day comp threshold cannot exceed the full-day threshold.")
        if (self.comp_off_min_hours is not None
                and self.comp_off_half_day_hours is not None
                and self.comp_off_min_hours > self.comp_off_half_day_hours):
            errors["comp_off_min_hours"] = (
                "The comp-off floor cannot exceed the half-day threshold.")

        # Arrival boundaries must run forwards through the day, or the status
        # ladder Present -> Late -> Half Day -> Absent becomes unreachable in
        # the middle.
        late, half = self.late_after_time, self.half_day_after_time
        if late is not None and self.office_start_time and late < self.office_start_time:
            errors["late_after_time"] = "Must not be before the office start time."
        if late is not None and half is not None and half <= late:
            errors["half_day_after_time"] = "Must be after late_after_time."
        if half is not None and self.absent_cutoff_time and half >= self.absent_cutoff_time:
            errors["half_day_after_time"] = "Must be before the absent cut-off."
        if errors:
            raise ValidationError(errors)


class PolicyAssignment(models.Model):
    """Binds a policy to everyone, a department, or one employee, over a window.

    Date-effective rather than merely prioritised, mirroring ``leaves.LeavePolicy``
    so the codebase has one policy-resolution idiom — and so re-deriving a past
    month applies the policy that was in force *then*.
    """

    class Scope(models.TextChoices):
        GLOBAL = "global", "Global"
        DEPARTMENT = "department", "Department"
        USER = "user", "User"

    # Most specific first. resolve_policy walks this order.
    PRIORITY = [Scope.USER, Scope.DEPARTMENT, Scope.GLOBAL]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy = models.ForeignKey(AttendancePolicy, on_delete=models.CASCADE, related_name="assignments")
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
    scope = models.CharField(max_length=12, choices=Scope.choices)
    department = models.ForeignKey(
        "leaves.Department", on_delete=models.CASCADE, null=True, blank=True,
        related_name="attendance_policy_assignments")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
        related_name="attendance_policy_assignments")
    effective_from = models.DateField()
    effective_until = models.DateField(
        null=True, blank=True,
        help_text="Leave empty for an open-ended assignment. Setting it is the "
                  "supported way to retire an assignment without losing history.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attendance_policy_assignments_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from"]
        indexes = [
            models.Index(fields=["scope", "effective_from"]),
            models.Index(fields=["user", "effective_from"]),
            models.Index(fields=["department", "effective_from"]),
        ]
        constraints = [
            # A row must point at exactly the target its scope implies.
            models.CheckConstraint(
                condition=(
                    Q(scope="global", department__isnull=True, user__isnull=True)
                    | Q(scope="department", department__isnull=False, user__isnull=True)
                    | Q(scope="user", department__isnull=True, user__isnull=False)
                ),
                name="attendance_policy_assignment_scope_target",
            ),
            # At most one *open-ended* assignment per target, so resolution can
            # never be ambiguous. Closed historical rows may overlap freely.
            models.UniqueConstraint(
                fields=["organization", "scope"], condition=Q(scope="global", effective_until__isnull=True),
                name="uniq_open_global_policy_assignment",
            ),
            models.UniqueConstraint(
                fields=["organization", "department"], condition=Q(scope="department", effective_until__isnull=True),
                name="uniq_open_department_policy_assignment",
            ),
            models.UniqueConstraint(
                fields=["organization", "user"], condition=Q(scope="user", effective_until__isnull=True),
                name="uniq_open_user_policy_assignment",
            ),
        ]

    def __str__(self):
        target = self.user or self.department or "everyone"
        return f"{self.policy} -> {target} from {self.effective_from}"

    def clean(self):
        errors = {}
        if self.effective_until and self.effective_from and self.effective_until < self.effective_from:
            errors["effective_until"] = "Must not be before effective_from."
        if self.scope == self.Scope.USER and not self.user_id:
            errors["user"] = "A user-scoped assignment needs a user."
        if self.scope == self.Scope.DEPARTMENT and not self.department_id:
            errors["department"] = "A department-scoped assignment needs a department."
        if self.scope == self.Scope.GLOBAL and (self.user_id or self.department_id):
            errors["scope"] = "A global assignment must not target a user or department."
        if self.scope == self.Scope.USER and self.department_id:
            errors["department"] = "A user-scoped assignment must not also target a department."
        if self.scope == self.Scope.DEPARTMENT and self.user_id:
            errors["user"] = "A department-scoped assignment must not also target a user."
        if errors:
            raise ValidationError(errors)


class EmployeeShift(models.Model):
    """Puts one employee on one shift over a date window."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shift_assignments")
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
    # PROTECT: a shift with people on it must not vanish and silently move them
    # back to the policy default.
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="assignments")
    effective_from = models.DateField()
    effective_until = models.DateField(null=True, blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="shift_assignments_made")
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from"]
        indexes = [models.Index(fields=["user", "effective_from"])]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "effective_from"], name="uniq_employee_shift_start"),
            models.UniqueConstraint(
                fields=["user"], condition=Q(effective_until__isnull=True),
                name="uniq_open_employee_shift"),
        ]

    def __str__(self):
        return f"{self.user} -> {self.shift} from {self.effective_from}"

    def clean(self):
        if self.effective_until and self.effective_from and self.effective_until < self.effective_from:
            raise ValidationError({"effective_until": "Must not be before effective_from."})


class WFHRequest(models.Model):
    """Approved work-from-home days.

    Deliberately NOT a Leave: WFH consumes no balance and must never reach
    ``LeaveDayRecord``. It follows the same approve/reject shape so the UI feels
    familiar, but it shares no tables with the leave workflow.

    Approval alone is not attendance. A day only becomes WORK_FROM_HOME when the
    employee also browser-checks-in — otherwise approving a request would
    manufacture attendance for someone who never started work.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wfh_requests")
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True, default="")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="wfh_requests_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]
        indexes = [
            models.Index(fields=["user", "start_date", "end_date"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"WFH {self.user} {self.start_date}..{self.end_date} ({self.status})"

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Must not be before start_date."})
        constraints = [
            # Both were unique=True standalone: two tenants could never both
            # have a "general" shift, nor both call one "General Shift".
            models.UniqueConstraint(fields=["organization", "code"],
                                    name="uniq_shift_org_code"),
            models.UniqueConstraint(fields=["organization", "name"],
                                    name="uniq_shift_org_name"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"],
                                    name="uniq_attendance_policy_org_name"),
        ]

    def covers(self, day):
        return self.start_date <= day <= self.end_date
