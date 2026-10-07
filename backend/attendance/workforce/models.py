"""The attendance correction request — the only new table in Phase 9.

An employee disputes a recorded day (the device missed their punch, they forgot
to check out, they were at a client site). The department head confirms it
happened, HR applies it. The approved values are written with ``source=HR``,
which ``biometric.derivation._is_hr_locked`` already treats as untouchable, so a
correction survives every future punch import, mapping change and re-derivation
without any change to the engine.

Because an approved correction is HR-locked and therefore immune to
re-derivation, it must be undoable by hand. That is what the ``previous_*``
snapshot columns are for: they are the only route back.
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from tenancy.scoping import AllTenantsManager, TenantManager


def correction_attachment_path(instance, filename):
    """Unguessable per-file directory, mirroring ``memos.memo_attachment_path``.

    Downloads still go through an authenticated, role-scoped view — the path is
    defence in depth, not the access control.
    """
    # Phase S3: tenant-scoped, via the one shared standard in
    # tenancy.storage. The function NAME and LOCATION are unchanged, so
    # Django's serialised `upload_to` import path still resolves and NO
    # migration is needed. Files already stored keep their old paths and
    # still resolve -- the media view looks a file up by its stored name.
    from tenancy.storage import upload_path

    return upload_path(instance, filename, module="attendance",
                       kind="corrections")


class AttendanceCorrectionRequest(models.Model):
    """Employee -> Department Head -> HR -> the attendance row is updated."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending (Department Head)"
        MANAGER_APPROVED = "manager_approved", "Pending (HR)"
        HR_APPROVED = "hr_approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    # Statuses where the request is still moving through the workflow. Used by
    # the partial unique index: one open request per employee-day, but a day may
    # be corrected again later.
    OPEN_STATUSES = [Status.PENDING, Status.MANAGER_APPROVED]

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
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="attendance_corrections")
    attendance_date = models.DateField()

    # --- what is being asked for ------------------------------------------
    requested_check_in = models.DateTimeField(null=True, blank=True)
    requested_check_out = models.DateTimeField(null=True, blank=True)
    requested_status = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Optional explicit status. Empty means: derive it from the "
                  "requested times under the employee's policy.")
    reason = models.TextField()
    attachment = models.FileField(
        upload_to=correction_attachment_path, max_length=255,
        null=True, blank=True)
    remarks = models.CharField(max_length=255, blank=True, default="")

    # --- snapshot taken at submit time (the undo path) --------------------
    previous_check_in = models.DateTimeField(null=True, blank=True)
    previous_check_out = models.DateTimeField(null=True, blank=True)
    previous_status = models.CharField(max_length=20, blank=True, default="")
    previous_source = models.CharField(max_length=20, blank=True, default="")
    had_attendance_row = models.BooleanField(
        default=False,
        help_text="False means the correction created the row; reverting deletes it.")

    # --- workflow ---------------------------------------------------------
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attendance_corrections_to_review",
        help_text="Resolved at submit time from the employee's department head.")
    manager_action_at = models.DateTimeField(null=True, blank=True)
    manager_remarks = models.CharField(max_length=255, blank=True, default="")
    hr_actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attendance_corrections_finalised")
    hr_action_at = models.DateTimeField(null=True, blank=True)
    hr_remarks = models.CharField(max_length=255, blank=True, default="")
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attendance_corrections_rejected")
    rejection_reason = models.CharField(max_length=255, blank=True, default="")

    applied_at = models.DateTimeField(null=True, blank=True)
    reverted_at = models.DateTimeField(null=True, blank=True)
    attendance = models.ForeignKey(
        "attendance.Attendance", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="correction_requests")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-attendance_date", "-created_at"]
        indexes = [
            models.Index(fields=["status", "attendance_date"]),
            models.Index(fields=["employee", "attendance_date"]),
            models.Index(fields=["manager", "status"]),
        ]
        constraints = [
            # One request in flight per employee-day. History may repeat: a day
            # can be corrected, and corrected again months later.
            models.UniqueConstraint(
                fields=["employee", "attendance_date"],
                condition=Q(status__in=["pending", "manager_approved"]),
                name="uniq_open_attendance_correction",
            ),
            # A request that asks for nothing is not a request.
            models.CheckConstraint(
                condition=(Q(requested_check_in__isnull=False)
                           | Q(requested_check_out__isnull=False)
                           | ~Q(requested_status="")),
                name="attendance_correction_requests_something",
            ),
        ]

    def __str__(self):
        return f"Correction {self.employee} {self.attendance_date} ({self.status})"

    # -- state helpers -----------------------------------------------------
    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def is_applied(self):
        return self.status == self.Status.HR_APPROVED and self.applied_at is not None

    def clean(self):
        errors = {}
        if not (self.requested_check_in or self.requested_check_out
                or self.requested_status):
            errors["reason"] = ("Request at least one of: check-in time, "
                                "check-out time, or status.")
        if (self.requested_check_in and self.requested_check_out
                and self.requested_check_out <= self.requested_check_in):
            errors["requested_check_out"] = "Must be after the requested check-in."
        for field in ("requested_check_in", "requested_check_out"):
            value = getattr(self, field)
            if value is not None and timezone.is_naive(value):
                errors[field] = ("Timestamps must carry an explicit UTC offset. "
                                 "A naive time would be read as UTC and land "
                                 "5h45m out for Asia/Kathmandu.")
            if value is not None and self.attendance_date:
                local_day = timezone.localtime(value).date()
                if local_day != self.attendance_date:
                    errors[field] = (f"Falls on {local_day}, not the requested "
                                     f"date {self.attendance_date}.")
        if self.attendance_date and self.attendance_date > timezone.localdate():
            errors["attendance_date"] = "Cannot correct a day that has not happened."
        if errors:
            raise ValidationError(errors)
