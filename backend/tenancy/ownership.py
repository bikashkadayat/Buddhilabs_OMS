"""Who owns a transactional row, and how that is proved.

PHASE A vs PHASE B
------------------
A Phase A row (a Department, a LeaveType, a sequence counter) is owned
DIRECTLY: there is nothing above it, so the organization is read from context
when it is created and that is the end of it.

A Phase B row is different. A TaskComment belongs to a Task; a MemoAttachment
belongs to a Memo; a CircularAcknowledgement belongs to a CircularRecipient
which belongs to a Circular. For those the organization is not a fact to be
looked up -- it is a fact already recorded one level up, and the only correct
answer is the parent's.

That matters for two reasons, and the second is the security one:

1. **Correctness with no request.** A comment created by a cron job, a data
   migration or a management command has no tenant in context. Deriving from
   the parent is right there, where context would be absent or wrong.

2. **It makes a cross-tenant attach impossible.** If a comment's organization
   is derived from its task, there is no way to file tenant A's comment against
   tenant B's task -- the row would simply be owned by B. And when BOTH are
   supplied and they disagree, that is not a default to resolve, it is an
   attack or a bug: ``CrossTenantWrite`` is raised. A column that is merely
   *populated* proves nothing; a column that is *derived and checked* does.

THE CHAIN IS DECLARED, NOT GUESSED
----------------------------------
``OWNERSHIP`` below maps each Phase B model to the attribute its organization
comes from. It is data, so it can be -- and is -- checked against the real
models by ``tenancy/tests/test_ownership.py``: every Phase B model must either
appear here with a resolvable path, or be listed in ``CONTEXT_OWNED`` as a root
that legitimately has no parent.
"""
import logging

from .exceptions import CrossTenantWrite

logger = logging.getLogger(__name__)

# model label -> the attribute (or dotted path) whose organization this row
# inherits. Every entry is a NON-NULL foreign key, so the parent always exists.
OWNERSHIP = {
    # --- tasks ---------------------------------------------------------
    "tasks.TaskAssignee": "task",
    "tasks.TaskAttachment": "task",
    "tasks.TaskChecklistGroup": "task",
    "tasks.TaskChecklistItem": "task",
    "tasks.TaskComment": "task",
    "tasks.TaskCommentMention": "comment",
    "tasks.TaskDependency": "task",
    "tasks.TaskSubtask": "task",
    # --- memos ---------------------------------------------------------
    "memos.MemoApprovalStep": "memo",
    "memos.MemoArchiveAccess": "memo",
    "memos.MemoAssignmentTransfer": "memo",
    "memos.MemoAttachment": "memo",
    "memos.MemoNote": "memo",
    "memos.MemoNoteRecipient": "note_round",
    "memos.MemoSection": "memo",
    "memos.MemoStepUnavailability": "memo",
    "memos.MemoWorkflowStep": "memo",
    # --- minutes -------------------------------------------------------
    "minutes.MinuteAttachment": "minute",
    "minutes.MinuteInvolvement": "minute",
    "minutes.MinuteParticipant": "minute",
    # --- circulars -----------------------------------------------------
    "circulars.CircularAcknowledgement": "recipient",
    "circulars.CircularAttachment": "circular",
    "circulars.CircularBroadcast": "circular",
    "circulars.CircularRecipient": "circular",
    "circulars.CircularWorkflowStep": "circular",
    # --- inventory -----------------------------------------------------
    "inventory.AssetDisposalAttachment": "disposal",
    "inventory.AssetTransferAttachment": "transfer",
    # --- leaves --------------------------------------------------------
    # Every one of these is per-employee, and users.User is Phase A.
    "leaves.CompensatoryLedger": "user",
    "leaves.EnterpriseLeaveBalance": "user",
    "leaves.Leave": "user",
    "leaves.LeaveBalance": "user",
    "leaves.LeaveDayRecord": "user",
    # --- attendance ----------------------------------------------------
    "attendance.Attendance": "employee",
    "attendance.AttendanceCorrectionRequest": "employee",
    "attendance.WFHRequest": "user",
    # The device is the owner, not the employee: an UNMAPPED punch has no
    # employee at all, and the terminal is the only thing that says which
    # tenant the data came from (see biometric.push_views).
    "biometric.AttendancePunch": "device",
    # --- appraisal -----------------------------------------------------
    "appraisal.Appraisal": "cycle",
    "appraisal.CompetencyRating": "appraisal",
    "appraisal.DevelopmentPlan": "appraisal",
    "appraisal.Goal": "appraisal",
    "appraisal.TrainingPlan": "appraisal",
    # --- notifications -------------------------------------------------
    "notifications.Notification": "recipient",
}

# --- Phase C (Phase S5) --------------------------------------------------
# Append-only logs, derived summaries, drafts and report records. Every one of
# these has a concrete parent, so they are as ordinary as any Phase B child --
# with ONE exception, audit.AuditLog, which reaches its subject through a
# GenericForeignKey and is handled by audit.services.log_action instead.
OWNERSHIP.update({
    # audit trails with a concrete parent
    "appraisal.AppraisalAuditLog": "appraisal",
    "appraisal.EvidenceReference": "appraisal",
    "circulars.CircularAuditLog": "circular",
    "circulars.CircularReadLog": "recipient",
    "memos.MemoNoteAudit": "note_round",
    "minutes.MinuteAuditLog": "minute",
    "tasks.TaskAuditLog": "task",
    # activity logs
    "biometric.DeviceSyncLog": "device",
    "tasks.TaskAttachmentDownload": "attachment",
    "tasks.TaskReminderLog": "task",
    "notifications.NotificationLog": "recipient",
    "notifications.NotificationPreference": "user",
    # asset lifecycle events
    "inventory.AssetDisposalEvent": "disposal",
    "inventory.AssetLifecycleEvent": "item",
    "inventory.AssetTransferEvent": "transfer",
    # derived summaries and snapshots
    "leaves.MonthlyLeaveSummary": "user",
    "leaves.WeeklyLeaveSummary": "user",
    "tasks.EmployeeTaskEvidenceSnapshot": "employee",
    # reports and drafts
    "reports.ReportRun": "requested_by",
    "reports.ScheduledReport": "created_by",
    "drafts.DocumentDraft": "owner",
    "drafts.DocumentDraftVersion": "draft",
})

# Four of those chains are NULLABLE foreign keys, so the parent cannot always
# supply the owner: AssetLifecycleEvent.item, NotificationLog.recipient,
# ReportRun.requested_by, ScheduledReport.created_by. Their `organization` is
# nullable for that reason and they fall back to context. Listed so the test
# suite can tell a deliberate nullable chain from a mistake.
NULLABLE_CHAINS = frozenset({
    "inventory.AssetLifecycleEvent",
    "notifications.NotificationLog",
    "reports.ReportRun",
    "reports.ScheduledReport",
})

# The one model with no chain at all. See AUDITLOG_STRATEGY below.
GENERIC_OWNED = frozenset({"audit.AuditLog"})


# Phase B roots: no non-null parent, so the organization comes from context at
# creation exactly as a Phase A row's does. Listed explicitly so the test suite
# can tell "this is a root" from "somebody forgot to declare the chain".
CONTEXT_OWNED = frozenset({
    "tasks.Task",
    "memos.Memo",
    "minutes.Minute",
    "circulars.Circular",
    "documents.IssuedDocument",
    # The inventory register and its workflow records. `item` is nullable on
    # several of these (an AssetRequest may name a CATEGORY rather than a
    # specific asset), so none of them has a parent that always exists.
    "inventory.InventoryItem",
    "inventory.ItemAssignment",
    "inventory.TakeOutRequest",
    "inventory.AssetRequest",
    "inventory.AssetReturn",
    "inventory.MaintenanceTicket",
    "inventory.AssetTransfer",
    "inventory.AssetDisposal",
})


def label_of(model):
    return f"{model._meta.app_label}.{model.__name__}"


def parent_path(model):
    """The declared ownership attribute for this model, or None."""
    return OWNERSHIP.get(label_of(model))


def organization_from_parent(instance):
    """The organization this row inherits, or None if it has no declared parent.

    Reads the parent's ``organization_id`` through the FK's *attname* where
    possible, so a parent that is already loaded is not re-fetched.
    """
    path = parent_path(type(instance))
    if path is None:
        return None

    target = instance
    for attribute in path.split("."):
        target = getattr(target, attribute, None)
        if target is None:
            return None
    return getattr(target, "organization_id", None)


def enforce(instance):
    """Derive and verify this row's organization. Returns the organization id.

    Three outcomes:

      * no declared parent      -> returns whatever the row already had
      * parent known, row blank -> the row INHERITS the parent's organization
      * both known and DIFFERENT -> ``CrossTenantWrite``

    The third is the point of this function. It is the check that turns
    "the column is filled in" into "the column is correct", and it is what
    makes filing one tenant's child row against another tenant's parent
    impossible rather than merely unlikely.
    """
    parent_org = organization_from_parent(instance)
    own_org = getattr(instance, "organization_id", None)

    if parent_org is None:
        return own_org

    if own_org is None:
        instance.organization_id = parent_org
        return parent_org

    if own_org != parent_org:
        raise CrossTenantWrite(
            f"{label_of(type(instance))} is owned by organization {own_org} "
            f"but its {parent_path(type(instance))} belongs to organization "
            f"{parent_org}. A child row may not cross tenants.")
    return own_org


# ---------------------------------------------------------------------------
# PHASE C PREPARATION -- the AuditLog ownership strategy (Part 9)
# ---------------------------------------------------------------------------
#
# AuditLog is NOT migrated in Phase S4. This is the design it will be migrated
# with, written down now so Phase S5 implements a reviewed decision instead of
# inventing one.
#
# WHY IT CANNOT USE THE MECHANISM ABOVE
# -------------------------------------
# Every chain in OWNERSHIP walks a concrete foreign key. AuditLog reaches its
# subject through a GENERIC foreign key:
#
#     content_type = FK(ContentType)
#     object_id    = CharField
#     content_object = GenericForeignKey(...)
#
# There is no join to follow and no database-level guarantee that the target
# still exists -- audit rows deliberately outlive what they describe. So the
# organization cannot be DERIVED at read time, and it cannot be enforced by a
# constraint against the parent.
#
# THE STRATEGY: STAMP AT WRITE TIME, IN THE ONE WRITER
# ----------------------------------------------------
# `audit.services.log_action()` is the INTENDED single writer -- the AuditLog
# model docstring says "written only via audit.services.log_action()". It is
# very nearly true, and the exception matters:
#
#     audit/services.py:24    log_action()           the sanctioned writer
#     drafts/services.py:51   drafts.services._log() writes AuditLog DIRECTLY
#
# Phase S5 must route `drafts.services._log` through `log_action` (or give it
# the same stamping), or draft lifecycle events will be the one kind of audit
# row with no owner. Verified by grepping for `AuditLog.objects.create`; the
# other hits are the per-module trails (TaskAuditLog, MemoNoteAudit,
# CircularAuditLog, MinuteAuditLog, AppraisalAuditLog), which are separate
# Phase C models with their OWN concrete parents and so need no generic
# strategy at all -- they inherit from their task/memo/circular/minute.
#
# With that one caller fixed, write-time stamping is safe, so the strategy is:
#
#     def log_action(actor, action, instance=None, changes=None, request=None,
#                    organization=None):
#         organization = (
#             organization                                   # 1. explicit
#             or getattr(instance, "organization_id", None)  # 2. the subject
#             or getattr(actor, "organization_id", None)     # 3. the actor
#             or active_organization_id(required=False)       # 4. context
#         )
#
# In that order, and the order is the point:
#
#   1. explicit wins, for the few callers that know better than the defaults;
#   2. THE SUBJECT, not the actor -- "platform staff suspended tenant X" is an
#      event in tenant X's history, and an impersonating platform user must not
#      file it under their own (nonexistent) organization;
#   3. the actor, for events with no subject at all (a failed login names an
#      email address, not a row);
#   4. context last, because it is the weakest signal and absent in cron.
#
# TWO CASES THAT MUST STAY UNSCOPED
# ---------------------------------
# * A FAILED LOGIN for an address that belongs to nobody. There is no tenant,
#   and inventing one would hide the probe. These rows get organization NULL,
#   which is why AuditLog.organization will be NULLABLE where Phase A/B are
#   NOT NULL -- the same exception users.User already has, for the same reason:
#   a check constraint is more precise than NOT NULL here.
# * PLATFORM-LEVEL events (a plan edited, a tenant provisioned) legitimately
#   belong to no tenant.
#
# WHAT PHASE S5 MUST ALSO DO
# --------------------------
# * Backfill. For rows whose `content_object` still resolves, read the
#   organization off it; for the rest, fall back to the actor's. Anything left
#   stays NULL and is reported, not guessed -- an audit row with a WRONG owner
#   is worse than one with none.
# * Scope the reader. audit/views.py is HR/Admin-only and currently unscoped;
#   it must filter by organization, and `users.roles.FORENSIC_READ_ROLES`
#   (which gates IP addresses and user agents) must become tenant-aware too.
# * Keep it append-only. The organization is set on INSERT and never updated,
#   so a tenant boundary cannot be rewritten after the fact.
AUDITLOG_STRATEGY = "stamp at write time in audit.services.log_action; see above"

# Phase C models that already have a concrete parent and therefore need no
# generic strategy -- their chain is as ordinary as any Phase B child's.
# Written down now so Phase S5 does not re-derive it.
PHASE_C_CHAINS = {
    "audit.AuditLog": None,                      # generic FK -- see above
    "circulars.CircularAuditLog": "circular",
    "circulars.CircularReadLog": "recipient",
    "memos.MemoNoteAudit": "note_round",
    "minutes.MinuteAuditLog": "minute",
    "tasks.TaskAuditLog": "task",
    "tasks.TaskAttachmentDownload": "attachment",
    "tasks.TaskReminderLog": "task",
    "appraisal.AppraisalAuditLog": "appraisal",
    "appraisal.EvidenceReference": "appraisal",
    "biometric.DeviceSyncLog": "device",
    "notifications.NotificationLog": "recipient",
    "notifications.NotificationPreference": "user",
    "inventory.AssetLifecycleEvent": "item",
    "inventory.AssetTransferEvent": "transfer",
    "inventory.AssetDisposalEvent": "disposal",
    "leaves.WeeklyLeaveSummary": "user",
    "leaves.MonthlyLeaveSummary": "user",
    "tasks.EmployeeTaskEvidenceSnapshot": "employee",
    "reports.ReportRun": "requested_by",
    "reports.ScheduledReport": "created_by",
    "drafts.DocumentDraft": "owner",
    "drafts.DocumentDraftVersion": "draft",
}


def resolve_for_audit(actor=None, instance=None, organization=None):
    """The organization an AuditLog row belongs to (Phase S5, Part 2).

    Resolution order, and the order is the substance:

      1. an EXPLICIT organization, for the few callers that know better;
      2. THE SUBJECT's organization -- "platform staff suspended tenant X" is
         an event in tenant X's history, and an impersonating platform user
         must not file it under their own (nonexistent) organization;
      3. the ACTOR's, for events with no subject at all (a failed login names
         an email address, not a row);
      4. the tenant in CONTEXT, last, because it is the weakest signal and
         absent in cron -- and NOT consulted at all for a platform actor, see
         below.

    Returns None when nothing resolves -- a failed login for an address
    belonging to nobody, or a genuine platform event. That is why
    AuditLog.organization is nullable: inventing a tenant for a probe would
    hide it, and filing a platform action under a customer would be a lie.

    A PLATFORM ACTOR NEVER FALLS THROUGH TO THE CONTEXT (Phase S6), and the
    bug that forced this is worth recording.

    Step 4 ends in ``active_organization_id``, which while TENANCY_ENABLED is
    False answers with the single-tenant default. So a platform operator
    signing in -- no subject with a tenant, no organization of their own --
    had their LOGIN event filed under NIF. Not a crash: a quietly wrong row in
    a customer's audit trail, saying a person who does not work for them
    logged into their workspace. Row-level security is what surfaced it, by
    refusing to write a NIF-owned row on a connection serving the platform.

    The subject still wins (step 2), so "platform staff suspended tenant X"
    remains an event in X's history. What is refused is INVENTING a tenant for
    a platform action that names none.
    """
    if organization is not None:
        return getattr(organization, "pk", organization)

    subject_org = getattr(instance, "organization_id", None)
    if subject_org is not None:
        return subject_org

    actor_org = getattr(actor, "organization_id", None)
    if actor_org is not None:
        return actor_org

    if getattr(actor, "is_platform_staff", False):
        # A platform operator belongs to no tenant, and nothing about the
        # ambient context makes this event one customer's business.
        return None

    from .scoping import active_organization_id

    return active_organization_id(required=False)
