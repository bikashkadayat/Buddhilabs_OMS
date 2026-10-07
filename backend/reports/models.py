import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from tenancy.scoping import AllTenantsManager, TenantManager


def report_upload_path(instance, filename):
    # Phase S3: tenant-scoped, via the one shared standard in
    # tenancy.storage. The function NAME and LOCATION are unchanged, so
    # Django's serialised `upload_to` import path still resolves and NO
    # migration is needed. Files already stored keep their old paths and
    # still resolve -- the media view looks a file up by its stored name.
    from tenancy.storage import upload_path

    return upload_path(instance, filename, module="reports")


class ReportType(models.TextChoices):
    EMPLOYEE_REGISTER = "employee_register", "Employee Leave Register"
    MONTHLY_ATTENDANCE = "monthly_attendance", "Monthly Attendance Report"
    LEAVE_UTILIZATION = "leave_utilization", "Leave Utilization Report"
    COMPLIANCE = "compliance", "Compliance Report"
    AUDIT_TRAIL = "audit_trail", "Audit Trail Report"
    # Workforce reports (Phase 9). Adding members to a TextChoices is a
    # Python-level change: the column stays varchar(40) and no DDL is emitted.
    ATTENDANCE_VS_LEAVE = "attendance_vs_leave", "Attendance vs Leave"
    ATTENDANCE_VS_WFH = "attendance_vs_wfh", "Attendance vs WFH"
    OVERTIME_SUMMARY = "overtime_summary", "Overtime Summary"
    LATE_ARRIVAL_SUMMARY = "late_arrival_summary", "Late Arrival Summary"
    SHIFT_UTILIZATION = "shift_utilization", "Shift Utilization"
    DEPARTMENT_ATTENDANCE = "department_attendance", "Department Attendance"
    COMP_OFF_REPORT = "comp_off_report", "Comp Off Summary"
    MONTHLY_WORKFORCE_SUMMARY = "monthly_workforce_summary", "Monthly Workforce Summary"
    # Analytics exports (Phase 10). Same story as Phase 9: adding members to a
    # TextChoices is a Python-level change, the column stays varchar(40) and the
    # migration touches no data.
    EXECUTIVE_SUMMARY = "executive_summary", "Executive Summary"
    DEPARTMENT_ANALYTICS = "department_analytics", "Department Analytics"
    ATTENDANCE_ANALYTICS = "attendance_analytics", "Attendance Analytics"
    OVERTIME_ANALYTICS = "overtime_analytics", "Overtime Analytics"
    WORKFORCE_ANALYTICS = "workforce_analytics", "Workforce Analytics"
    # Governance (Phase DEPARTMENT-GOVERNANCE-HARDENING). The register of who
    # answers for each department - the question every departmental workflow
    # ends at.
    DEPARTMENT_OWNERSHIP = "department_ownership", "Department Ownership Report"


# Report types a department head may run, always narrowed to their own
# department by the view. Everything else stays HR/Admin only.
WORKFORCE_REPORT_TYPES = [
    "attendance_vs_leave", "attendance_vs_wfh", "overtime_summary",
    "late_arrival_summary", "shift_utilization", "department_attendance",
    "comp_off_report", "monthly_workforce_summary",
]

# Analytics exports (Phase 10). Department heads may run all of these EXCEPT the
# executive summary, which is an organisation-wide document by definition — a
# department-scoped "executive summary" would be a misleading title on a partial
# picture. Scope narrowing for the rest is injected by the view, exactly as it
# already is for the workforce reports.
ANALYTICS_REPORT_TYPES = [
    "executive_summary", "department_analytics", "attendance_analytics",
    "overtime_analytics", "workforce_analytics",
]
MANAGER_ANALYTICS_REPORT_TYPES = [
    "department_analytics", "attendance_analytics", "overtime_analytics",
    "workforce_analytics",
]


# Governance reports (Phase DEPARTMENT-GOVERNANCE-HARDENING). Organisation-wide
# by nature - the register only means anything whole - so they are not in the
# department-head set.
GOVERNANCE_REPORT_TYPES = ["department_ownership"]


# Which output formats each report supports (first is the default).
REPORT_FORMATS = {
    ReportType.EMPLOYEE_REGISTER: ["excel"],
    ReportType.MONTHLY_ATTENDANCE: ["excel", "pdf"],
    ReportType.LEAVE_UTILIZATION: ["pdf"],
    ReportType.COMPLIANCE: ["pdf"],
    ReportType.AUDIT_TRAIL: ["excel"],
    # Every workforce report supports all three formats.
    **{ReportType(value): ["excel", "pdf", "csv"] for value in WORKFORCE_REPORT_TYPES},
    # Analytics exports are format-specific by design: each is laid out for the
    # medium it targets (a narrative one-pager, a multi-sheet workbook, a flat
    # table), so offering all three would ship three broken variants of each.
    ReportType.EXECUTIVE_SUMMARY: ["pdf"],
    ReportType.DEPARTMENT_ANALYTICS: ["pdf"],
    ReportType.ATTENDANCE_ANALYTICS: ["excel"],
    ReportType.OVERTIME_ANALYTICS: ["excel"],
    ReportType.WORKFORCE_ANALYTICS: ["csv"],
    # The ownership register is a table with no narrative and no chart, so all
    # three media carry it equally well.
    ReportType.DEPARTMENT_OWNERSHIP: ["excel", "pdf", "csv"],
}


class ReportRun(models.Model):
    """A single report generation request and its resulting file."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        GENERATING = "generating", "Generating"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). DELIBERATELY NULLABLE --
    # its `requested_by` is nullable (scheduled runs).
    # A NULL-organization row is invisible under the RLS policy, which is the
    # correct fail-closed answer: only the platform console reads it.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True, null=True, blank=True,
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
    report_type = models.CharField(max_length=40, choices=ReportType.choices)
    params = models.JSONField(default=dict, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="report_runs",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    file = models.FileField(upload_to=report_upload_path, max_length=255,
                            null=True, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def file_url(self):
        return self.file.url if self.file else None

    def __str__(self):
        return f"{self.get_report_type_display()} ({self.status})"


class ScheduledReport(models.Model):
    """A recurring report definition delivered by email."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). DELIBERATELY NULLABLE --
    # its `created_by` is nullable.
    # A NULL-organization row is invisible under the RLS policy, which is the
    # correct fail-closed answer: only the platform console reads it.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True, null=True, blank=True,
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
    report_type = models.CharField(max_length=40, choices=ReportType.choices)
    params = models.JSONField(default=dict, blank=True)
    recipients = models.JSONField(default=list, blank=True)  # list of email addresses
    # Supports @hourly / @daily / @weekly / @monthly shortcuts (see command).
    cron_expression = models.CharField(max_length=64, default="@daily")
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="scheduled_reports",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["report_type"]

    def __str__(self):
        return f"{self.get_report_type_display()} -> {', '.join(self.recipients)} ({self.cron_expression})"
