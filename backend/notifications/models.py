import uuid

from django.conf import settings
from django.db import models
from tenancy.scoping import AllTenantsManager, TenantManager


class Category(models.TextChoices):
    MEMO_ASSIGNED_TO_REVIEW = "MEMO_ASSIGNED_TO_REVIEW", "Memo assigned to review"
    MEMO_APPROVED = "MEMO_APPROVED", "Memo approved"
    MEMO_REJECTED = "MEMO_REJECTED", "Memo rejected"
    MEMO_RETURNED = "MEMO_RETURNED", "Memo returned"
    # Memo workflow (Phase 9). One category per stage rather than reusing
    # MEMO_ASSIGNED_TO_REVIEW for all of them, so a recipient can mute
    # "recommendation required" without also muting final approvals, and so the
    # notification text can name the action actually being asked for.
    MEMO_SUBMITTED = "MEMO_SUBMITTED", "Memo submitted"
    MEMO_REVIEW_REQUIRED = "MEMO_REVIEW_REQUIRED", "Memo review required"
    MEMO_RECOMMENDATION_REQUIRED = "MEMO_RECOMMENDATION_REQUIRED", "Memo recommendation required"
    MEMO_SUPPORT_REQUIRED = "MEMO_SUPPORT_REQUIRED", "Memo support required"
    MEMO_APPROVAL_REQUIRED = "MEMO_APPROVAL_REQUIRED", "Memo approval required"
    MEMO_ARCHIVED = "MEMO_ARCHIVED", "Memo archived"
    # Phase 49.5. Noting is informational and non-blocking, so it gets its own
    # category rather than reusing an approval one - somebody who wants approval
    # traffic may reasonably not want to be told about every memo they have merely
    # been asked to read. The unavailability alert goes to whoever has to fix it,
    # which is a different audience again.
    MEMO_NOTE_REQUIRED = "MEMO_NOTE_REQUIRED", "Memo note required"
    MEMO_APPROVER_UNAVAILABLE = "MEMO_APPROVER_UNAVAILABLE", "Memo approver unavailable"
    MEMO_ACCESS_GRANTED = "MEMO_ACCESS_GRANTED", "Memo archive access granted"
    # Circular workflow (Phase 50). Broadcast is separated from the acknowledgement
    # categories because they reach different audiences for different reasons: a
    # broadcast is an announcement everybody gets, an acknowledgement request is a
    # task for the same people, and somebody may reasonably mute the reminders
    # without muting the circulars themselves.
    CIRCULAR_REVIEW_REQUIRED = "CIRCULAR_REVIEW_REQUIRED", "Circular review required"
    CIRCULAR_ISSUE_REQUIRED = "CIRCULAR_ISSUE_REQUIRED", "Circular issue required"
    CIRCULAR_ISSUED = "CIRCULAR_ISSUED", "Circular issued"
    CIRCULAR_RETURNED = "CIRCULAR_RETURNED", "Circular returned for revision"
    CIRCULAR_BROADCAST = "CIRCULAR_BROADCAST", "Circular broadcast"
    CIRCULAR_EXECUTIVE = "CIRCULAR_EXECUTIVE", "Executive circular"
    BOARD_NOTICE = "BOARD_NOTICE", "Board-level notification"
    CIRCULAR_ACK_REQUIRED = "CIRCULAR_ACK_REQUIRED", "Circular acknowledgement required"
    CIRCULAR_ACK_REMINDER = "CIRCULAR_ACK_REMINDER", "Circular acknowledgement reminder"
    # Minute workflow (Phase 31+). One category per stage, mirroring the memo
    # categories, so a recipient can mute "minute support required" without muting
    # memo approvals. Acknowledgement has its own categories because it is a separate
    # lifecycle from approval - somebody may want approval traffic but not the
    # acknowledgement reminders, or the reverse.
    MINUTE_ASSIGNED = "MINUTE_ASSIGNED", "Minute assigned"
    MINUTE_SUBMITTED = "MINUTE_SUBMITTED", "Minute submitted"
    MINUTE_REVIEW_REQUIRED = "MINUTE_REVIEW_REQUIRED", "Minute review required"
    MINUTE_RECOMMENDATION_REQUIRED = "MINUTE_RECOMMENDATION_REQUIRED", "Minute recommendation required"
    MINUTE_SUPPORT_REQUIRED = "MINUTE_SUPPORT_REQUIRED", "Minute support required"
    MINUTE_APPROVAL_REQUIRED = "MINUTE_APPROVAL_REQUIRED", "Minute approval required"
    MINUTE_APPROVED = "MINUTE_APPROVED", "Minute approved"
    MINUTE_REJECTED = "MINUTE_REJECTED", "Minute returned"
    MINUTE_UPDATED = "MINUTE_UPDATED", "Minute updated"
    MINUTE_ARCHIVED = "MINUTE_ARCHIVED", "Minute archived"
    MINUTE_ACK_REQUIRED = "MINUTE_ACK_REQUIRED", "Minute acknowledgement required"
    MINUTE_ACKNOWLEDGED = "MINUTE_ACKNOWLEDGED", "Minute acknowledged"
    MINUTE_ACK_REMINDER = "MINUTE_ACK_REMINDER", "Minute acknowledgement reminder"
    MINUTE_ACTION_ASSIGNED = "MINUTE_ACTION_ASSIGNED", "Minute action item assigned"
    # Separate categories per escalation level, so someone can mute routine reminders
    # without also muting the management alert that means a task is badly overdue.
    MINUTE_ACTION_ESCALATED = "MINUTE_ACTION_ESCALATED", "Minute action item escalated"
    MINUTE_ACTION_ALERT = "MINUTE_ACTION_ALERT", "Minute action item management alert"
    MINUTE_RESOLUTION = "MINUTE_RESOLUTION", "Minute resolution recorded"
    LEAVE_SUBMITTED = "LEAVE_SUBMITTED", "Leave submitted"
    # Phase BOD-ROLE-EXECUTIVE-GOVERNANCE - the three the brief names.
    LEAVE_BOARD_APPROVAL_REQUIRED = (
        "LEAVE_BOARD_APPROVAL_REQUIRED", "Department Head leave approval")
    LEAVE_APPROVED = "LEAVE_APPROVED", "Leave approved"
    LEAVE_REJECTED = "LEAVE_REJECTED", "Leave rejected"
    LEAVE_BALANCE_LOW = "LEAVE_BALANCE_LOW", "Leave balance low"
    POLICY_UPDATED = "POLICY_UPDATED", "Policy updated"
    SYSTEM_ANNOUNCEMENT = "SYSTEM_ANNOUNCEMENT", "System announcement"
    # Billing decisions, in the workspace as well as by email: the customer's
    # administrator sees what happened to a payment where they work.
    PAYMENT_APPROVED = "PAYMENT_APPROVED", "Payment approved"
    PAYMENT_REJECTED = "PAYMENT_REJECTED", "Payment rejected"
    PAYMENT_INFO_REQUESTED = "PAYMENT_INFO_REQUESTED", "Payment needs more information"
    WEEKLY_DIGEST = "WEEKLY_DIGEST", "Weekly digest"
    INVENTORY_TAKEOUT_REQUESTED = "INVENTORY_TAKEOUT_REQUESTED", "Inventory take-out requested"
    INVENTORY_TAKEOUT_APPROVED = "INVENTORY_TAKEOUT_APPROVED", "Inventory take-out approved"
    INVENTORY_TAKEOUT_REJECTED = "INVENTORY_TAKEOUT_REJECTED", "Inventory take-out rejected"
    # Asset lifecycle (Phase 70). One category per point at which SOMEBODY HAS TO
    # ACT or has just been made accountable for an asset - not one per transition.
    # A workflow that notifies on every step trains people to ignore it, and the
    # step that actually needed them is the one they then miss.
    INVENTORY_ASSET_REQUESTED = "INVENTORY_ASSET_REQUESTED", "Asset requested"
    INVENTORY_ASSET_APPROVAL_REQUIRED = "INVENTORY_ASSET_APPROVAL_REQUIRED", "Asset request awaiting inventory"
    INVENTORY_ASSET_READY = "INVENTORY_ASSET_READY", "Asset ready for collection"
    INVENTORY_ASSET_HANDED_OVER = "INVENTORY_ASSET_HANDED_OVER", "Asset handed over — confirm receipt"
    INVENTORY_ASSET_ACCEPTED = "INVENTORY_ASSET_ACCEPTED", "Asset receipt confirmed"
    INVENTORY_ASSET_REJECTED = "INVENTORY_ASSET_REJECTED", "Asset request refused"
    INVENTORY_RETURN_REQUESTED = "INVENTORY_RETURN_REQUESTED", "Asset return raised"
    INVENTORY_RETURN_COMPLETED = "INVENTORY_RETURN_COMPLETED", "Asset return closed"
    INVENTORY_MAINTENANCE_REPORTED = "INVENTORY_MAINTENANCE_REPORTED", "Asset fault reported"
    INVENTORY_MAINTENANCE_DONE = "INVENTORY_MAINTENANCE_DONE", "Asset repair completed"
    # Phase ASSET-CUSTODY-TRANSFER.
    INVENTORY_TRANSFER_SUBMITTED = "INVENTORY_TRANSFER_SUBMITTED", "Asset transfer submitted"
    INVENTORY_TRANSFER_APPROVAL_REQUIRED = "INVENTORY_TRANSFER_APPROVAL_REQUIRED", "Asset transfer approval required"
    INVENTORY_TRANSFER_APPROVED = "INVENTORY_TRANSFER_APPROVED", "Asset transfer approved"
    INVENTORY_TRANSFER_REJECTED = "INVENTORY_TRANSFER_REJECTED", "Asset transfer rejected"
    INVENTORY_TRANSFER_COMPLETED = "INVENTORY_TRANSFER_COMPLETED", "Asset transfer completed"
    INVENTORY_ASSET_ASSIGNED = "INVENTORY_ASSET_ASSIGNED", "Asset assigned to you"
    # Phase ASSET-LIFECYCLE-DISPOSAL.
    INVENTORY_DISPOSAL_APPROVAL_REQUIRED = "INVENTORY_DISPOSAL_APPROVAL_REQUIRED", "Asset disposal approval required"
    INVENTORY_DISPOSAL_APPROVED = "INVENTORY_DISPOSAL_APPROVED", "Asset disposal approved at a stage"
    INVENTORY_DISPOSAL_REJECTED = "INVENTORY_DISPOSAL_REJECTED", "Asset disposal rejected"
    INVENTORY_DISPOSAL_COMPLETED = "INVENTORY_DISPOSAL_COMPLETED", "Asset disposed"
    INVENTORY_WARRANTY_EXPIRING = "INVENTORY_WARRANTY_EXPIRING", "Asset warranty expiring"
    INVENTORY_AMC_EXPIRING = "INVENTORY_AMC_EXPIRING", "Asset maintenance contract expiring"
    INVENTORY_END_OF_LIFE = "INVENTORY_END_OF_LIFE", "Asset reaching end of life"
    # Workforce management (Phase 9). No email template needed: _template_for()
    # falls through to emails/generic.html for anything unrecognised.
    ATTENDANCE_CORRECTION_SUBMITTED = "ATTENDANCE_CORRECTION_SUBMITTED", "Attendance correction submitted"
    ATTENDANCE_CORRECTION_MANAGER_APPROVED = "ATTENDANCE_CORRECTION_MANAGER_APPROVED", "Attendance correction approved by department head"
    ATTENDANCE_CORRECTION_APPROVED = "ATTENDANCE_CORRECTION_APPROVED", "Attendance correction applied"
    ATTENDANCE_CORRECTION_REJECTED = "ATTENDANCE_CORRECTION_REJECTED", "Attendance correction rejected"
    WFH_SUBMITTED = "WFH_SUBMITTED", "Work-from-home requested"
    WFH_APPROVED = "WFH_APPROVED", "Work-from-home approved"
    WFH_REJECTED = "WFH_REJECTED", "Work-from-home rejected"
    COMP_OFF_CONFIRMED = "COMP_OFF_CONFIRMED", "Compensatory day confirmed"
    # Task Management (Phase T1). One category per point at which SOMEBODY HAS
    # TO ACT, not one per transition — a workflow that notifies on every step
    # trains people to ignore it, and the step that actually needed them is then
    # the one they miss. So there is no notification for "started" or "progress
    # updated": nobody is being asked for anything by either.
    TASK_CREATED = "TASK_CREATED", "Task created"
    TASK_ASSIGNED = "TASK_ASSIGNED", "Task assigned"
    TASK_ACCEPTED = "TASK_ACCEPTED", "Task accepted"
    TASK_CLARIFICATION_REQUESTED = "TASK_CLARIFICATION_REQUESTED", "Task clarification requested"
    TASK_REVIEW_REQUIRED = "TASK_REVIEW_REQUIRED", "Task review required"
    TASK_APPROVED = "TASK_APPROVED", "Task approved"
    TASK_REWORK_REQUESTED = "TASK_REWORK_REQUESTED", "Task rework requested"
    TASK_CLOSED = "TASK_CLOSED", "Task closed"
    TASK_BLOCKED = "TASK_BLOCKED", "Task blocked"
    TASK_CANCELLED = "TASK_CANCELLED", "Task cancelled"
    TASK_COMMENTED = "TASK_COMMENTED", "Task comment added"
    # Phase TASK-MULTI-ASSIGNEE-COLLABORATION, narrowed by
    # TASK-COLLABORATION-HARDENING. SHARED work only, and only when one person
    # FINISHES their part: that is the point at which the others can see the
    # submission coming and whoever is left becomes the one being waited for.
    # Every intermediate figure is on the task, live, and notifying each one
    # sent two dozen messages per task that asked nobody for anything. On a solo
    # task nothing is sent at all — see tasks.workflow.update_progress.
    TASK_PROGRESS_UPDATED = "TASK_PROGRESS_UPDATED", "Task progress updated"
    # Phase T2.3. Being NAMED in a comment is a different event from a
    # comment being posted on something you follow: it is addressed to you,
    # and somebody who has muted general task chatter still wants it.
    TASK_MENTIONED = "TASK_MENTIONED", "Mentioned in a task comment"
    # Phase T4. The reminder and escalation engines.
    #
    # A reminder and an escalation are separate categories because they reach
    # different people for different reasons: a reminder is addressed to whoever
    # has to do the work, an escalation tells somebody ELSE that it has not been
    # done. Somebody may reasonably mute the first and must not be able to mute
    # the second without noticing, so they are not one switch.
    TASK_DUE_REMINDER = "TASK_DUE_REMINDER", "Task due soon"
    TASK_OVERDUE = "TASK_OVERDUE", "Task overdue"
    TASK_ESCALATED = "TASK_ESCALATED", "Task escalated"
    TASK_REVIEW_REMINDER = "TASK_REVIEW_REMINDER", "Task review pending"
    # Phase TASK-AUTOSAVE-AND-SUBTASKS. Same rule: one per point at which
    # somebody has to act. Reopened, edited, reordered and deleted subtasks
    # are on the task and in its timeline; nobody is asked for anything by them.
    TASK_SUBTASK_ASSIGNED = "TASK_SUBTASK_ASSIGNED", "Subtask assigned"
    TASK_SUBTASK_COMPLETED = "TASK_SUBTASK_COMPLETED", "Subtask completed"
    TASK_SUBTASK_OVERDUE = "TASK_SUBTASK_OVERDUE", "Subtask overdue"
    TASK_DRAFT_RECOVERABLE = "TASK_DRAFT_RECOVERABLE", "Unsaved task draft"

    # --- Appraisal (Phase RELEASE) -------------------------------------
    # The module ran correctly and told nobody: an employee only discovered
    # their self-assessment was due by visiting the page. Ten categories, one
    # per moment somebody's turn actually begins or ends — not one per
    # transition, because half the transitions change whose turn it is without
    # anybody needing to hear about it.
    APPRAISAL_OPENED = "APPRAISAL_OPENED", "Appraisal opened"
    APPRAISAL_GOALS_SUBMITTED = ("APPRAISAL_GOALS_SUBMITTED",
                                 "Goals submitted for approval")
    APPRAISAL_GOALS_APPROVED = ("APPRAISAL_GOALS_APPROVED", "Goals approved")
    APPRAISAL_SELF_ASSESSMENT_DUE = ("APPRAISAL_SELF_ASSESSMENT_DUE",
                                     "Self assessment due")
    APPRAISAL_REVIEW_PENDING = ("APPRAISAL_REVIEW_PENDING",
                                "Appraisal review pending")
    APPRAISAL_COMMITTEE_REVIEW = ("APPRAISAL_COMMITTEE_REVIEW",
                                  "Appraisal committee review")
    APPRAISAL_FEEDBACK_READY = ("APPRAISAL_FEEDBACK_READY",
                                "Appraisal feedback ready")
    APPRAISAL_RETURNED = "APPRAISAL_RETURNED", "Appraisal returned for changes"
    APPRAISAL_CLOSED = "APPRAISAL_CLOSED", "Appraisal closed"
    APPRAISAL_TRAINING_DECIDED = ("APPRAISAL_TRAINING_DECIDED",
                                  "Training request decided")
    APPRAISAL_DEADLINE_REMINDER = ("APPRAISAL_DEADLINE_REMINDER",
                                   "Appraisal deadline approaching")


class Notification(models.Model):
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
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications", db_index=True,
    )
    category = models.CharField(max_length=40, choices=Category.choices, db_index=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    action_url = models.CharField(max_length=500, blank=True, default="")
    is_read = models.BooleanField(default=False, db_index=True)
    is_email_sent = models.BooleanField(default=False)
    # Optional dedup key; two notifications with the same (recipient, key) collapse.
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="uniq_notification_idempotency",
            ),
        ]

    def __str__(self):
        return f"{self.category} -> {self.recipient} ({'read' if self.is_read else 'unread'})"


class NotificationPreference(models.Model):
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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences",
    )
    category = models.CharField(max_length=40, choices=Category.choices)
    in_app_enabled = models.BooleanField(default=True)
    # Weekly digest is opt-in; everything else defaults on (see get_preference).
    email_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "category"], name="uniq_user_category_pref"),
        ]

    def __str__(self):
        return f"{self.user} / {self.category}: in_app={self.in_app_enabled} email={self.email_enabled}"


class NotificationLog(models.Model):
    """
    Audit + retry log for outbound emails. One row per send attempt (sent or
    failed with the error), so failures are visible and retryable rather than
    swallowed silently. Written by the email worker; failures to write here are
    themselves swallowed so logging can never break the workflow.
    """
    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). DELIBERATELY NULLABLE --
    # its `recipient` is nullable.
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
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="notification_logs",
    )
    recipient_email = models.EmailField(blank=True, default="")
    category = models.CharField(max_length=40)
    # Free-form reference to the source object (e.g. the leave UUID) for auditing.
    object_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    subject = models.CharField(max_length=255, blank=True, default="")
    # The human document number where the module has one (CIR-2026-000001,
    # a memo number). `object_id` above is the UUID; this is what a person
    # would quote, and the two answer different questions in an audit.
    reference = models.CharField(max_length=64, blank=True, default="", db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    @property
    def module(self):
        """The module this send belongs to, from the category prefix.

        Derived rather than stored: categories are named MODULE_EVENT, so a
        column would be a second copy of information the category already
        carries — and the copy is what goes stale.
        """
        from .emails import module_for
        return module_for(self.category)

    def __str__(self):
        return f"{self.recipient_email} {self.category} [{self.status}]"
