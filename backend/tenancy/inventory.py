"""The tenant-isolation inventory: which model gets ``organization_id``, and when.

WHY THIS IS CODE AND NOT A DOCUMENT
-----------------------------------
A migration plan in a wiki page goes stale the first time somebody adds a
model. This module is checked by ``tenancy/tests/test_inventory.py``, which
walks ``django.apps.apps.get_models()`` and fails if ANY model in the project
is missing from the classification below. So a new model added next month
cannot be quietly forgotten: CI stops and the author has to say which phase it
belongs to.

It is also the seed of the Phase S2/S3 conformance test. Once columns start
landing, the same registry drives "every model in PHASE_A has an organization
FK, a composite unique constraint, and an RLS policy".

PHASE A / B / C IS A SEQUENCING ORDER, NOT AN EXEMPTION
-------------------------------------------------------
Every model in A, B and C will get ``organization_id`` and an RLS policy.
Nothing here is optional. The letters say WHEN:

  A  Identity, org structure, configuration, taxonomies, NUMBER SEQUENCES.
     Everything else references these. Until the sequences in particular are
     scoped, two tenants share one counter and each sees unexplained gaps in
     its own document numbering.

  B  Transactional records and their children. These attach to Phase A roots,
     so doing A first means B's backfill can derive the owner from its parent
     instead of guessing.

  C  Append-only logs, derived summaries and caches. Last because they are
     either re-derivable or append-only, so an inconsistency discovered
     mid-migration is recoverable. NOT low-risk: AuditLog carries client IPs
     and user agents, which users/roles.py restricts to HR and Admin, and it
     reaches its target through a GenericForeignKey -- so its organization must
     be stamped at write time in audit.services.log_action, not inferred.

  PLATFORM_GLOBAL  Genuinely not tenant data. The ONLY exemption, and the one
     list a reviewer has to read carefully.
"""

# ---------------------------------------------------------------------------
# PHASE A -- identity, structure, configuration, taxonomies, sequences
# ---------------------------------------------------------------------------
PHASE_A = frozenset({
    # identity
    "users.User",
    # organisational structure
    "leaves.Department",
    # leave configuration / policy
    "leaves.LeaveType", "leaves.LeavePolicy", "leaves.Holiday",
    "leaves.CalendarEvent", "leaves.EntitlementRule",
    # attendance configuration
    "attendance.Shift", "attendance.AttendancePolicy",
    "attendance.PolicyAssignment", "attendance.EmployeeShift",
    # asset / document / task configuration and taxonomies
    "inventory.InventoryCategory", "memos.MemoTemplate", "minutes.MinuteType",
    "tasks.TaskTemplate", "tasks.TaskTemplateGroup", "tasks.TaskTemplateItem",
    "tasks.TaskGroup", "tasks.DepartmentGoal",
    # appraisal configuration
    "appraisal.Competency", "appraisal.AppraisalCycle",
    # biometric hardware registry
    "biometric.BiometricDevice", "biometric.BiometricEmployee",
    # NUMBER SEQUENCES -- the highest-value items in this phase
    "tasks.TaskNumberSequence", "memos.MemoNumberSequence",
    "minutes.MinuteNumberSequence", "circulars.CircularNumberSequence",
    "inventory.InventorySequence",
})

# ---------------------------------------------------------------------------
# PHASE B -- transactional records and their children
# ---------------------------------------------------------------------------
PHASE_B = frozenset({
    # leave
    "leaves.Leave", "leaves.LeaveBalance", "leaves.EnterpriseLeaveBalance",
    "leaves.LeaveDayRecord", "leaves.CompensatoryLedger",
    # memo
    "memos.Memo", "memos.MemoApprovalStep", "memos.MemoWorkflowStep",
    "memos.MemoAttachment", "memos.MemoSection", "memos.MemoNote",
    "memos.MemoNoteRecipient", "memos.MemoStepUnavailability",
    "memos.MemoAssignmentTransfer", "memos.MemoArchiveAccess",
    # minute
    "minutes.Minute", "minutes.MinuteParticipant", "minutes.MinuteInvolvement",
    "minutes.MinuteAttachment",
    # circular
    "circulars.Circular", "circulars.CircularWorkflowStep",
    "circulars.CircularBroadcast", "circulars.CircularRecipient",
    "circulars.CircularAcknowledgement", "circulars.CircularAttachment",
    # task
    "tasks.Task", "tasks.TaskAssignee", "tasks.TaskDependency",
    "tasks.TaskChecklistGroup", "tasks.TaskChecklistItem", "tasks.TaskSubtask",
    "tasks.TaskAttachment", "tasks.TaskComment", "tasks.TaskCommentMention",
    # inventory / assets
    "inventory.InventoryItem", "inventory.ItemAssignment",
    "inventory.TakeOutRequest", "inventory.AssetRequest",
    "inventory.AssetReturn", "inventory.MaintenanceTicket",
    "inventory.AssetTransfer", "inventory.AssetTransferAttachment",
    "inventory.AssetDisposal", "inventory.AssetDisposalAttachment",
    # attendance records
    "attendance.Attendance", "attendance.WFHRequest",
    "attendance.AttendanceCorrectionRequest", "biometric.AttendancePunch",
    # appraisal records
    "appraisal.Appraisal", "appraisal.Goal", "appraisal.CompetencyRating",
    "appraisal.DevelopmentPlan", "appraisal.TrainingPlan",
    # notifications / documents
    "notifications.Notification", "documents.IssuedDocument",
})

# ---------------------------------------------------------------------------
# PHASE C -- append-only logs, derived summaries, caches
# ---------------------------------------------------------------------------
PHASE_C = frozenset({
    # audit trails. AuditLog needs its organization STAMPED AT WRITE TIME --
    # a GenericForeignKey gives nothing to derive it from.
    "audit.AuditLog", "circulars.CircularAuditLog", "circulars.CircularReadLog",
    "memos.MemoNoteAudit", "minutes.MinuteAuditLog", "tasks.TaskAuditLog",
    "appraisal.AppraisalAuditLog",
    # activity logs
    "tasks.TaskAttachmentDownload", "tasks.TaskReminderLog",
    "biometric.DeviceSyncLog", "notifications.NotificationLog",
    # asset lifecycle events
    "inventory.AssetLifecycleEvent", "inventory.AssetTransferEvent",
    "inventory.AssetDisposalEvent",
    # derived summaries / snapshots
    "leaves.WeeklyLeaveSummary", "leaves.MonthlyLeaveSummary",
    "tasks.EmployeeTaskEvidenceSnapshot",
    # reports and drafts
    "reports.ReportRun", "reports.ScheduledReport",
    "drafts.DocumentDraft", "drafts.DocumentDraftVersion",
    # misc
    "appraisal.EvidenceReference", "notifications.NotificationPreference",
})

# ---------------------------------------------------------------------------
# PLATFORM GLOBAL -- the only models that never get an organization column
# ---------------------------------------------------------------------------
PLATFORM_GLOBAL = frozenset({
    # this app: the tenant registry and the billing records about tenants
    "tenancy.Organization", "tenancy.OrganizationSettings",
    "tenancy.OrganizationBranding", "tenancy.Plan", "tenancy.PlanPrice",
    "tenancy.Subscription", "tenancy.SubscriptionEvent", "tenancy.Payment",
    "tenancy.PaymentInstruction",
    # Phase S6: the platform's own record of what it did TO a tenant.
    # Deliberately NOT tenant data and deliberately NOT policed by RLS -- see
    # tenancy.models.PlatformAuditLog for the three reasons. It carries an
    # `organization` column, which is why it has to be named here explicitly:
    # the conformance sweep classifies by column, and would otherwise read
    # that column as a missing policy.
    "tenancy.PlatformAuditLog",
    # Phase S6.5: the platform's record of an export it produced FOR a
    # tenant. Same reasoning as PlatformAuditLog -- it carries an
    # `organization` column and is still not tenant data, because a customer
    # must not be able to discover that an export of their workspace exists,
    # let alone read one.
    "tenancy.TenantExport",
    # Phase S7: a registration that has not been verified yet. It cannot be
    # tenant data -- no tenant exists while the row matters, which is the
    # whole point of Part 3's "no tenant created until verification".
    "tenancy.PendingRegistration",
    # Customer success: what a customer sent the platform team (a question,
    # a problem, a feature request, a rating). The customer talking TO the
    # platform, read from the console -- not tenant data, by the same reasoning
    # as Payment.
    "tenancy.SupportRequest",
    # Phase S6.75: daily platform counters. Carries an `organization` column
    # and is still not tenant data -- it holds no identifying detail, and it
    # exists precisely so the console can draw a dashboard WITHOUT being able
    # to read tenant rows. See tenancy.models.PlatformMetric.
    "tenancy.PlatformMetric",
    # Phase S9: hostname -> organization. Read by the resolver on every
    # request with no tenant bound, which is only possible for a table
    # outside the row-level security policies -- the same reason
    # `Organization` itself is here.
    "tenancy.TenantDomain",
    # Django and third-party infrastructure
    "contenttypes.ContentType", "auth.Permission", "auth.Group",
    "sessions.Session", "admin.LogEntry",
    "token_blacklist.OutstandingToken", "token_blacklist.BlacklistedToken",
})

TENANT_SCOPED = PHASE_A | PHASE_B | PHASE_C
ALL_CLASSIFIED = TENANT_SCOPED | PLATFORM_GLOBAL


# ---------------------------------------------------------------------------
# Migration progress
# ---------------------------------------------------------------------------
#
# ALL THREE TIERS ARE DONE. All 106 business models carry `organization`, every
# global unique constraint has been replaced by a per-organization one, every
# model's default manager scopes reads, and -- on PostgreSQL -- a row-level
# security policy enforces the boundary in the database regardless of which
# manager or raw query produced the statement.
#
# Six models keep a NULLABLE organization, each for a recorded reason: see
# NULLABLE_ORGANIZATION below.
#
# users.User is the 28th and its column is deliberately still NULLABLE at the
# field level, because a PLATFORM account must have no organization. The rule
# is enforced by the `user_tenant_has_organization` check constraint instead,
# which is strictly stronger than NOT NULL: it also forbids the reverse
# mistake.
PHASE_A_COMPLETE = True
PHASE_B_COMPLETE = True   # Phase S4
PHASE_C_COMPLETE = True   # Phase S5
RLS_ENFORCED = True       # Phase S5 -- PostgreSQL only; see tenancy/rls.py

# Models whose `organization` column is intentionally nullable, and why. A
# NULL-organization row is invisible under the RLS policy -- which is the right
# fail-closed answer for a record that belongs to no tenant.
NULLABLE_ORGANIZATION = {
    "users.User":
        "a PLATFORM account must have no organization; the rule is enforced "
        "by the user_tenant_has_organization check constraint instead, which "
        "is strictly stronger than NOT NULL because it also forbids the "
        "reverse mistake",
    "audit.AuditLog":
        "a failed login for an address belonging to nobody has no tenant, and "
        "inventing one would hide the probe; genuine platform events (a plan "
        "edited, a tenant provisioned) have none either",
    "inventory.AssetLifecycleEvent":
        "its `item` foreign key is nullable, so the chain cannot always supply "
        "an owner -- an event may be recorded before an asset row exists",
    "notifications.NotificationLog":
        "its `recipient` foreign key is nullable, so a delivery attempt to an "
        "address with no account behind it has no tenant to belong to",
    "reports.ReportRun":
        "its `requested_by` is nullable -- a scheduled run has no requester",
    "reports.ScheduledReport":
        "its `created_by` is nullable, so a schedule that outlives the account "
        "that created it keeps working rather than losing its owner",
}


# ---------------------------------------------------------------------------
# Raw SQL audit (Phase S5, Part 7)
# ---------------------------------------------------------------------------
#
# Every `cursor.execute()` outside migrations and tests, classified. Under RLS
# the classification is not advisory: a statement that must obey RLS and is run
# by a BYPASSRLS role silently crosses tenants, and one that must NOT obey it
# and is run by the application role silently returns nothing.
RAW_SQL_AUDIT = {
    "config/health_views.py:_db_up": {
        "statement": "SELECT 1",
        "class": "ALLOWED GLOBAL",
        "why": "a liveness probe. Touches no table, so no policy applies and "
               "none is needed.",
    },
    "monitoring/metrics.py:_database_metrics": {
        "statement": "SELECT 1",
        "class": "ALLOWED GLOBAL",
        "why": "the same probe, for the metrics endpoint.",
    },
    # --- Phase S6.75: the launch readiness audit ---
    "tenancy/launch.py:check_database": {
        "statement": "SELECT 1",
        "class": "ALLOWED GLOBAL",
        "why": "the same liveness probe, for the launch readiness report. "
               "Touches no table.",
    },
    "tenancy/launch.py:check_rls": {
        "statement": "SELECT rolbypassrls, rolsuper FROM pg_roles "
                     "WHERE rolname = current_user",
        "class": "ALLOWED GLOBAL",
        "why": "asks POSTGRES what the application's own role may do, which "
               "is the one question the ORM cannot answer and the whole "
               "point of the check: TENANCY_RLS_ENABLED=True while connected "
               "as a BYPASSRLS role installs every policy and enforces none. "
               "Reads a system catalogue about the current user only; no "
               "tenant table is involved and no tenant data is reachable "
               "through it.",
    },
    "monitoring/management/commands/backup_verify.py": {
        "statement": "SELECT COUNT(*) FROM <business table>",
        "class": "PLATFORM ADMIN ONLY",
        "why": "row counts across EVERY tenant are a platform integrity "
               "question. Under RLS the application role would count 0 on "
               "every table and the command would report that the backup "
               "proves nothing -- a verifier that silently passes. It now "
               "refuses to run unless the role can see across tenants.",
    },
    "tenancy/rls.py": {
        "statement": "SET LOCAL app.current_org / pg_class / pg_policies reads",
        "class": "ALLOWED GLOBAL",
        "why": "the mechanism itself. Binds the tenant and introspects the "
               "policies; reads no business row.",
    },
    "conftest.py:_bind_on_connection_created": {
        "statement": "SET app.current_org",
        "class": "ALLOWED GLOBAL",
        "why": "the TEST HARNESS standing in for TenantResolutionMiddleware. "
               "A test that calls the ORM directly has no request to bind the "
               "tenant, and the binding is per CONNECTION -- so it is "
               "attached to connection creation. Reads no business row, and "
               "ships in no deployment.",
    },
    "tenancy/console.py:system_health": {
        "statement": "SELECT 1",
        "class": "ALLOWED GLOBAL",
        "why": "the same liveness probe as config/health_views, on the "
               "platform operations panel. Touches no table. Phase S6 (Part "
               "1) needs it because 'is the database up' is a widget on the "
               "platform dashboard, and asking the ORM would answer through "
               "a table that has a policy.",
    },
}


# ---------------------------------------------------------------------------
# Unique constraints that broke multi-tenancy -- and what replaced them
# ---------------------------------------------------------------------------
#
# Phase A entries are now a RECORD of the rewrite, not a to-do list: each maps
# the old global constraint to the per-organization one that replaced it. The
# Phase B/C entries are still outstanding.
#
# Migration ordering, for whoever does Phase B: ADD the composite constraint,
# THEN drop the global one. Dropping first leaves a window with no uniqueness at
# all, during which a duplicate can be written that then stops the new index
# from ever building.

# field -> replacement constraint name. Phase A: all applied.
PHASE_A_UNIQUE_REWRITES = {
    "leaves.Department.code": "uniq_department_org_code",
    "leaves.LeaveType.code": "uniq_leavetype_org_code",
    "leaves.Holiday.date": "uniq_holiday_org_date",
    "leaves.CalendarEvent.(date,name)": "uniq_calendar_event_org_date_name",
    "leaves.EntitlementRule.(category,leave_type)": "uniq_entitlement_org_category_type",
    "attendance.Shift.code": "uniq_shift_org_code",
    "attendance.Shift.name": "uniq_shift_org_name",
    "attendance.AttendancePolicy.name": "uniq_attendance_policy_org_name",
    "attendance.PolicyAssignment.(scope)": "uniq_open_global_policy_assignment",
    "inventory.InventoryCategory.name": "uniq_inventory_category_org_name",
    "inventory.InventorySequence.(key,year)": "unique_together(organization,key,year)",
    "memos.MemoNumberSequence.(type_code,year)": "unique_together(organization,type_code,year)",
    "minutes.MinuteType.code": "uniq_minute_type_org_code",
    "minutes.MinuteNumberSequence.year": "uniq_minute_sequence_org_year",
    "circulars.CircularNumberSequence.year": "uniq_circular_sequence_org_year",
    "tasks.TaskNumberSequence.year": "uniq_task_sequence_org_year",
    "tasks.TaskTemplate.name": "task_template_org_name_unique",
    "appraisal.AppraisalCycle.name": "uniq_appraisal_cycle_org_name",
    "appraisal.Competency.code": "uniq_competency_org_code",
    "biometric.BiometricDevice.name": "uniq_biometric_device_org_name",
    "biometric.BiometricDevice.label": "uniq_biometric_device_org_label",
    "users.User.username": "uniq_user_org_username + uniq_platform_user_username",
    "users.User.email": "uniq_user_org_email_ci + uniq_platform_user_email_ci",
    "users.User.employee_id": "uniq_user_org_employee_id",
}

# ---------------------------------------------------------------------------
# Unique constraints that break multi-tenancy
# ---------------------------------------------------------------------------
#
# Every entry is a GLOBAL uniqueness that two tenants would collide on. The fix
# is the same shape each time: drop `unique=True`, add
# UniqueConstraint(fields=["organization", <field>]).
#
# Migration ordering matters and is not obvious: ADD the composite constraint,
# deploy, THEN drop the global one. Dropping first leaves a window with no
# uniqueness at all, during which a duplicate can be written that then blocks
# the new index from building.
# NOTHING OUTSTANDING in Phase A or B. The twelve document-number columns that
# were here after Phase S3 are composite as of Phase S4 -- see
# PHASE_B_UNIQUE_REWRITES below.
#
# The only globally-unique field left in the project by design is
# biometric.BiometricDevice.serial_number, and that one is CORRECT: the iClock
# protocol resolves a terminal by serial with no tenant in hand, so serial ->
# tenant must be a function platform-wide. See INTENTIONALLY_GLOBAL.
GLOBAL_UNIQUE_FIELDS = {}

# Fields that are globally unique ON PURPOSE. The exemption list a reviewer has
# to read carefully, with the reason attached to each entry.
# Keyed by "<model label>:<constraint name>", because the exemption is carried
# by a Meta UniqueConstraint rather than by unique=True on the field.
INTENTIONALLY_GLOBAL = {
    "biometric.BiometricDevice:uniq_biometric_device_serial_global":
        "The iClock/ADMS protocol has no authentication: a terminal sends "
        "?SN=<serial> and nothing else, so the serial IS the credential and is "
        "resolved BEFORE any tenant is known. serial -> tenant must be a "
        "function, or one tenant registering another's serial diverts its "
        "punches. Contrast inventory.InventoryItem.serial_number, which is "
        "per-tenant for exactly the opposite reason.",
}

# Phase B rewrites applied in Phase S4: old global field -> new constraint.
PHASE_B_UNIQUE_REWRITES = {
    "tasks.Task.task_number": "uniq_task_number_org",
    "memos.Memo.memo_number": "uniq_memo_number_org",
    "minutes.Minute.minute_number": "uniq_minute_number_org",
    "circulars.Circular.circular_number": "uniq_circular_number_org",
    "documents.IssuedDocument.document_number": "uniq_document_number_org",
    "inventory.InventoryItem.asset_code": "uniq_asset_code_org",
    "inventory.InventoryItem.Lower(serial_number)": "uniq_inventory_serial_org",
    "inventory.TakeOutRequest.reference": "uniq_takeout_reference_org",
    "inventory.AssetRequest.reference": "uniq_asset_request_reference_org",
    "inventory.AssetReturn.reference": "uniq_asset_return_reference_org",
    "inventory.MaintenanceTicket.reference": "uniq_maintenance_reference_org",
    "inventory.AssetTransfer.transfer_number": "uniq_transfer_number_org",
    "inventory.AssetDisposal.disposal_number": "uniq_disposal_number_org",
}

# Composite constraints that are NOT already protected by a tenant-owned
# column. The other ~38 composite constraints in the project include `user`,
# `task`, `memo`, `item`, `circular`, `appraisal` or `device` -- all of which
# become tenant-scoped themselves -- so they are transitively safe and adding
# `organization` to them would be noise in an already large diff.
# NOTHING OUTSTANDING. `uniq_inventory_serial_number` -- the last entry, and
# the only Phase B composite that lacked a tenant-owned column -- became
# `uniq_inventory_serial_org` in Phase S4.
BROKEN_COMPOSITE_CONSTRAINTS = {}

# Constraints that CANNOT be added until the Phase S2 user backfill has run,
# because every existing row would violate them today.
# Nothing is deferred any more. `user_tenant_has_organization` -- the half of
# the tenant/platform XOR that Phase S1 could not enforce because every account
# still had organization = NULL -- was added in users.0014 once
# users.0013_phase_a_user_backfill had populated the column.
DEFERRED_CONSTRAINTS = {}

# The constraints Phase S2 added to users.User, as a checklist for review.
USER_IDENTITY_CONSTRAINTS = (
    "user_platform_staff_has_no_org",     # Phase S1
    "user_tenant_has_organization",       # Phase S2
    "uniq_user_org_username",
    "uniq_platform_user_username",
    "uniq_user_org_email_ci",
    "uniq_platform_user_email_ci",
    "uniq_user_org_employee_id",
)


def classify(label):
    """``'A' | 'B' | 'C' | 'PLATFORM' | None`` for an ``app_label.ModelName``."""
    if label in PHASE_A:
        return "A"
    if label in PHASE_B:
        return "B"
    if label in PHASE_C:
        return "C"
    if label in PLATFORM_GLOBAL:
        return "PLATFORM"
    return None


def summary():
    """Counts, for the phase report and the platform console."""
    return {
        "phase_a": len(PHASE_A),
        "phase_b": len(PHASE_B),
        "phase_c": len(PHASE_C),
        "tenant_scoped_total": len(TENANT_SCOPED),
        "platform_global": len(PLATFORM_GLOBAL),
        "global_unique_fields_outstanding": sum(
            len(v) for v in GLOBAL_UNIQUE_FIELDS.values()),
        "phase_b_unique_rewrites_applied": len(PHASE_B_UNIQUE_REWRITES),
        "phase_b_complete": PHASE_B_COMPLETE,
        "intentionally_global": len(INTENTIONALLY_GLOBAL),
        "broken_composite_constraints_outstanding": sum(
            len(v) for v in BROKEN_COMPOSITE_CONSTRAINTS.values()),
        "phase_a_unique_rewrites_applied": len(PHASE_A_UNIQUE_REWRITES),
        "phase_a_complete": PHASE_A_COMPLETE,
    }
