import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from tenancy.scoping import AllTenantsManager, TenantManager


class Attendance(models.Model):
    """One attendance record per employee per day (check-in/out based).

    Holiday / On-Leave statuses are derived at read time from the existing
    Holiday + Leave models, so those days need no pre-created rows; a stored row
    exists only when there is a real check-in or a manual HR entry.
    """

    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        LATE = "late", "Late"
        HALF_DAY = "half_day", "Half Day"
        ON_LEAVE = "on_leave", "On Leave"
        HOLIDAY = "holiday", "Holiday"
        # Phase 8. The ONLY new status: these values are mutually exclusive, so
        # overtime and comp-off eligibility are fields (below), not members —
        # a day can be Late AND have overtime AND earn a comp day.
        WORK_FROM_HOME = "wfh", "Work From Home"

    # Statuses HR may set by hand. WORK_FROM_HOME is excluded on purpose: it is
    # derived from an approved WFHRequest plus a real browser check-in, and
    # letting HR type it in would bypass both.
    MANUAL_STATUSES = [
        (Status.PRESENT, Status.PRESENT.label),
        (Status.ABSENT, Status.ABSENT.label),
        (Status.LATE, Status.LATE.label),
        (Status.HALF_DAY, Status.HALF_DAY.label),
        (Status.ON_LEAVE, Status.ON_LEAVE.label),
        (Status.HOLIDAY, Status.HOLIDAY.label),
    ]

    class MarkedBy(models.TextChoices):
        SELF = "self", "Self"
        HR = "hr", "HR"
        SYSTEM = "system", "System"

    class LocationSource(models.TextChoices):
        """HOW the coordinates were obtained, which is not the same question
        as who authored the row (`Source`, below).

        The browser's Geolocation API reports `coords.accuracy` but never
        says what produced the fix. A 20 m accuracy is a GPS lock; a 3,000 m
        accuracy is the device's IP address being looked up in a database,
        and the two deserve different trust even though both arrive through
        the same call. `UNKNOWN` is honest about the cases where the client
        did not say.
        """
        GPS = "gps", "Device GPS"
        NETWORK = "network", "Wi-Fi / cell network"
        IP = "ip", "IP address lookup"
        UNKNOWN = "unknown", "Not reported"

    class Source(models.TextChoices):
        """Who authored this row. Canonical from Phase 5 onward.

        ``marked_by`` is kept in lockstep (browser<->self, biometric<->system,
        hr<->hr) because the serializer and the React app already read it.
        """
        # The stored value stays "browser" -- every existing row, report and
        # client reads it -- and only the label changes to the brief's name.
        BROWSER = "browser", "Web app"
        MOBILE = "mobile", "Mobile app"
        BIOMETRIC = "biometric", "Biometric device"
        HR = "hr", "HR entry"

    # The two values an employee authors themselves. Derivation treats them
    # identically: both are snapshotted before a device punch is merged in,
    # and both are restored if the punches go away.
    SELF_SOURCES = frozenset({Source.BROWSER, Source.MOBILE})

    # Precedence for authoring a row: HR > Biometric > App. An HR correction
    # is never overwritten by the derivation engine — see biometric/derivation.py.
    SOURCE_PRECEDENCE = {Source.BROWSER: 0, Source.MOBILE: 0, Source.BIOMETRIC: 1, Source.HR: 2}
    MARKED_BY_FOR_SOURCE = {
        Source.BROWSER: MarkedBy.SELF,
        Source.MOBILE: MarkedBy.SELF,
        Source.BIOMETRIC: MarkedBy.SYSTEM,
        Source.HR: MarkedBy.HR,
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
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attendance_records"
    )
    date = models.DateField(db_index=True)
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)
    # Geolocation captured from the browser at check-in / check-out (best-effort:
    # null when the user denies permission or the device can't get a fix).
    # lat/lng use 6 decimal places (~0.11 m precision); accuracy is in metres.
    check_in_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_in_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_in_accuracy = models.FloatField(null=True, blank=True)
    check_in_address = models.CharField(max_length=255, blank=True, default="")
    check_out_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_out_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    check_out_accuracy = models.FloatField(null=True, blank=True)
    check_out_address = models.CharField(max_length=255, blank=True, default="")
    # Straight-line metres from the configured office at the moment of capture.
    # Stored (not derived at read time) so a later change to the office address
    # or radius can never rewrite what history says about an old check-in.
    # Null when no office is configured or no coordinates were captured.
    check_in_distance_m = models.FloatField(null=True, blank=True)
    check_out_distance_m = models.FloatField(null=True, blank=True)
    # --- provenance of each fix ---
    #
    # Accuracy alone does not say whether a coordinate came from satellites
    # or from an IP database, and "where was this employee" is a question
    # that gets asked in disputes. Stored per END OF THE DAY rather than per
    # record: an employee can check in on a phone outdoors and check out on
    # a desktop over Wi-Fi, and those two pins have different standing.
    check_in_location_source = models.CharField(
        max_length=10, choices=LocationSource.choices, blank=True, default="")
    check_out_location_source = models.CharField(
        max_length=10, choices=LocationSource.choices, blank=True, default="")
    # The device the employee actually used. Truncated rather than parsed:
    # the useful question in a dispute is "was this the same device both
    # times", and the raw string answers it without this model growing a
    # user-agent parser that needs updating for every new browser.
    check_in_user_agent = models.CharField(max_length=256, blank=True,
                                            default="")
    check_out_user_agent = models.CharField(max_length=256, blank=True,
                                             default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ABSENT)
    working_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    remarks = models.TextField(blank=True, default="")
    marked_by = models.CharField(max_length=10, choices=MarkedBy.choices, default=MarkedBy.SELF)

    # --- biometric derivation (Phase 5) — all additive, all optional ---------
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.BROWSER)
    first_punch_at = models.DateTimeField(null=True, blank=True)
    last_punch_at = models.DateTimeField(null=True, blank=True)
    punch_count = models.PositiveSmallIntegerField(
        default=0, help_text="All raw punches for the day, including breaks and overtime.")
    device = models.ForeignKey(
        "biometric.BiometricDevice", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attendance_records",
    )
    # A biometric punch overwrites check_in/check_out. Snapshotting the browser
    # values keeps that reversible: if the device mapping is later removed, the
    # employee's own check-in can be restored instead of silently lost.
    browser_check_in = models.DateTimeField(null=True, blank=True)
    browser_check_out = models.DateTimeField(null=True, blank=True)
    # WHICH SOURCE SUPPLIED EACH END OF THE DAY. With App + Biometric the
    # merge rule takes the earliest valid check-in and the latest valid
    # check-out from either source, so one row can have a check-in from the
    # phone and a check-out from the gate terminal. `source` above says who
    # authored the row; these say where each time came from.
    # Which app the employee's own times (browser_check_*) came from, so a
    # revert restores "Mobile app" rather than relabelling it "Web app".
    app_source = models.CharField(
        max_length=20, choices=Source.choices, blank=True, default="", db_default="")
    check_in_source = models.CharField(
        max_length=20, choices=Source.choices, blank=True, default="", db_default="")
    check_out_source = models.CharField(
        max_length=20, choices=Source.choices, blank=True, default="", db_default="")

    # --- policy engine (Phase 8) — all additive, all defaulted ---------------
    # working_hours above keeps its meaning (the GROSS span). These sit beside
    # it so existing reports and exports are untouched.
    #
    # db_default is deliberate on every NOT NULL column here. Django's stock
    # `ADD COLUMN ... DEFAULT x NOT NULL` is immediately followed by
    # `ALTER COLUMN ... DROP DEFAULT`, which backfills existing rows but leaves
    # the database with no default. Code that predates these columns would then
    # read fine but fail on INSERT with a not-null violation — breaking the
    # "roll back the code, keep the schema" recovery path. Keeping a real
    # database default makes that rollback work.
    regular_hours = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00"), db_default=Decimal("0.00"))
    overtime_hours = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00"), db_default=Decimal("0.00"),
        help_text="Hours beyond the policy threshold. Payroll-ready.")
    late_minutes = models.PositiveSmallIntegerField(
        default=0, db_default=0,
        help_text="Minutes late after the shift/policy grace period.")
    comp_off_days = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("0.00"), db_default=Decimal("0.00"),
        help_text="Comp days earned by working this Saturday/holiday: 0, 0.5 or 1.")
    comp_off_eligible = models.BooleanField(default=False, db_default=False)
    is_wfh = models.BooleanField(
        default=False, db_default=False,
        help_text="An approved WFH request covers this day. On its "
                  "own this is NOT attendance — a check-in is still required.")
    # Which rules actually ran, so a status can be explained months later.
    applied_policy = models.ForeignKey(
        "attendance.AttendancePolicy", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attendance_records")
    applied_shift = models.ForeignKey(
        "attendance.Shift", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attendance_records")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("employee", "date")
        ordering = ["-date"]
        indexes = [models.Index(fields=["employee", "date"])]

    def __str__(self):
        return f"{self.employee} · {self.date} · {self.status}"


# Registers the policy-engine models with the `attendance` app. Imported at the
# bottom so `Attendance` is defined first; the policy models never import back.
from .policy.models import (  # noqa: E402,F401  (re-export for `attendance.models`)
    AttendancePolicy,
    EmployeeShift,
    PolicyAssignment,
    Shift,
    WFHRequest,
)
from .workforce.models import AttendanceCorrectionRequest  # noqa: E402,F401
