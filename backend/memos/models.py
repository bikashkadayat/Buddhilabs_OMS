import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from tenancy.scoping import AllTenantsManager, TenantManager


def memo_attachment_path(instance, filename):
    """
    Store uploads under an unguessable per-file UUID directory
    (memos/attachments/YYYY/MM/<uuid>/<original-name>). Even if MEDIA were ever
    served directly, the path cannot be guessed; downloads still go through the
    authenticated MemoViewSet.attachment endpoint which enforces CanViewMemo.
    """
    # Phase S3: tenant-scoped, via the one shared standard in
    # tenancy.storage. The function NAME and LOCATION are unchanged, so
    # Django's serialised `upload_to` import path still resolves and NO
    # migration is needed. Files already stored keep their old paths and
    # still resolve -- the media view looks a file up by its stored name.
    from tenancy.storage import upload_path

    return upload_path(instance, filename, module="memos", kind="attachments")


class Memo(models.Model):
    """
    A memo document routed through a maker -> reviewer -> approver workflow.
    """
    class MemoType(models.TextChoices):
        """
        The manual's one Memo Type dropdown (E-memo-manual p.4).

        Three values, and each carries a rule rather than being a label:

          * GENERAL - readable by every concerned department (To, CC) through the
            process and into the archive, segregated by function;
          * CONFIDENTIAL - readable only by the people who hold a role on it
            (recommend / support / review / approve), confined to the sub-unit,
            unit or department;
          * DRAFT - a prospective memo circulated for feedback before it is raised
            formally. "This is only temporary and log record is not kept for this
            one", which is why `keeps_log` exists below and why the history writer
            consults it.

        This replaced two fields. `memo_type` used to carry a business category
        (internal / external / financial / hr / general) and a separate
        `classification` carried sensitivity; the manual has one dropdown, so
        sensitivity moved here and the business category - which the manual does
        not have - was retired.
        """
        GENERAL = "general", "GENERAL"
        CONFIDENTIAL = "confidential", "CONFIDENTIAL"
        DRAFT = "draft", "DRAFT"

    # Types that restrict who may read the memo (p.4). A GENERAL memo is open to
    # the departments involved; the other two are not.
    RESTRICTED_TYPES = frozenset({"confidential"})

    class Status(models.TextChoices):
        """
        The enterprise workflow states, in progression order.

        DRAFT -> DRAFT_FOR_REVIEW -> UNDER_REVIEW -> RECOMMENDED -> SUPPORTED
              -> APPROVED -> ARCHIVED, with REJECTED reachable from any pending
        state.

        CANCELLED is outside that ladder: it is the author withdrawing their own
        memo, not a stage anyone approves. It stays because withdrawing is real
        functionality, and a cancelled memo is terminal and read-only.

        The legacy SUBMITTED state was removed in migration 0011 - migration 0010
        converted every memo carrying it to DRAFT_FOR_REVIEW with a real workflow
        matrix. Historical MemoApprovalStep rows may still record a "submitted"
        ACTION; that is history and remains renderable.
        """
        DRAFT = "draft", "Draft"
        DRAFT_FOR_REVIEW = "draft_for_review", "Draft For Review"
        UNDER_REVIEW = "under_review", "Under Review"
        RECOMMENDED = "recommended", "Recommended"
        SUPPORTED = "supported", "Supported"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"
        CANCELLED = "cancelled", "Cancelled"

    # Statuses a memo can no longer be acted on or edited from. An archived memo
    # is read-only everywhere: serializer capability flags, the update/destroy
    # guards and the workflow engine all consult these two sets.
    TERMINAL_STATUSES = frozenset({"approved", "archived", "cancelled"})
    READ_ONLY_STATUSES = frozenset({"archived", "cancelled"})

    # Days a workflow step is expected to be actioned within. One figure, because
    # the manual's form has no priority to vary it by. Used only for the inbox's
    # Due Days column and its colour coding: nothing is auto-escalated or
    # auto-approved on expiry, so this shows a red row rather than changing a
    # memo's fate.
    DEFAULT_SLA_DAYS = 4

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

    memo_number = models.CharField(max_length=30, editable=False)
    subject = models.CharField(max_length=500)

    # "To: Memo approver's functional title (example: CEO)" (p.4). A functional
    # title rather than a person: the manual's own example is a post, and the
    # person who holds it is decided by the approval chain, which is built
    # separately. Free text for that reason - "CEO", "DCEO in absence of CEO".
    to_line = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Who the memo is addressed to, by functional title (e.g. CEO).")

    memo_type = models.CharField(
        max_length=20, choices=MemoType.choices, default=MemoType.GENERAL,
        db_index=True)

    # "Reference Memo: If the memo is related to another memo, add the reference
    # code of that memo" (p.4). Free text, not a foreign key: the referenced memo
    # may predate this system or live on paper, and a constraint here would only
    # stop people recording the truth.
    reference_number = models.CharField(
        max_length=100, blank=True, default="", db_index=True,
        help_text="The reference code of a related memo, if this memo cites one.")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memos_created", db_index=True)

    # NOTE: `current_reviewer` and `current_approver` used to live here. They were
    # the whole of the old two-slot routing model and could express exactly two
    # approvers. Migration 0009 converted every memo they routed into
    # MemoWorkflowStep rows and 0010 dropped them, so routing now has a single
    # representation. "Who is this with?" is answered by `active_step`.

    attachment = models.FileField(upload_to=memo_attachment_path,
                                  max_length=255, null=True, blank=True)

    # Department the memo belongs to, snapshotted from the author at creation.
    # Snapshotted rather than followed through created_by so a transfer or a
    # renamed unit never silently reassigns historical memos - the archive and
    # the PDF must keep reporting the department the memo was raised under.
    department = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memos", db_index=True,
    )
    department_name = models.CharField(
        max_length=150, blank=True, default="",
        help_text="Department label captured at creation. Populated even when the "
                  "author has only the legacy free-text department and no FK.",
    )

    # "Please select unit and sub-unit if access to view required is to be
    # limited" (p.4). leaves.Department is self-nesting through `parent`, so a
    # unit and a sub-unit are department rows further down the same tree - there
    # is no second table for them, and narrowing to a sub-unit narrows who may
    # read a restricted memo.
    department_unit = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memos_as_unit",
        help_text="Optional unit within the department, to narrow view access.")
    department_unit_name = models.CharField(max_length=150, blank=True, default="")
    department_sub_unit = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memos_as_sub_unit",
        help_text="Optional sub-unit, to narrow view access further.")
    department_sub_unit_name = models.CharField(
        max_length=150, blank=True, default="")

    # "CC: Please select related department, if required in cc. In case of general
    # memo, employees in departments of cc list can also view the archived memo"
    # (p.4). So CC is not decoration - on a GENERAL memo it is an access grant,
    # and memos.permissions reads it as one.
    cc_departments = models.ManyToManyField(
        "leaves.Department", blank=True, related_name="memos_cc",
        help_text="Departments copied in. On a GENERAL memo they may also read "
                  "it once archived.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    @property
    def keeps_log(self):
        """
        Whether this memo accumulates a history.

        A DRAFT memo does not: "This is only temporary and log record is not kept
        for this one" (p.4). Every history writer asks this rather than testing
        the type itself, so the rule has one home - and a draft that is later
        raised as a GENERAL or CONFIDENTIAL memo starts logging from that moment
        without anything having to backfill it.
        """
        return self.memo_type != self.MemoType.DRAFT

    @property
    def is_restricted(self):
        """
        True when this memo's type narrows who may read it (p.4).

        A property rather than the test repeated at each call site because both
        the queryset filter and the object-level permission need the same answer,
        and they are already required to agree.
        """
        return self.memo_type in self.RESTRICTED_TYPES

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The workflow menus all filter on status alongside one of these
            # columns; without the composite indexes every menu is a full scan
            # of the memo table once the archive grows.
            models.Index(fields=["status", "created_by"], name="memo_status_author_idx"),
            models.Index(fields=["status", "department"], name="memo_status_dept_idx"),
            models.Index(fields=["memo_type"], name="memo_type_idx"),
            models.Index(fields=["-created_at"], name="memo_created_desc_idx"),
        ]
        # Phase S4: was unique=True on `memo_number` alone -- a GLOBAL namespace.
        # Phase S3 already gives each tenant its own document prefix, so a
        # collision was impossible in practice; the composite makes it
        # impossible by construction and removes the last way one tenant's
        # numbering could refuse another tenant's insert.
        constraints = [
            models.UniqueConstraint(fields=["organization", "memo_number"],
                                    name="uniq_memo_number_org"),
        ]

    @property
    def is_read_only(self):
        """Archived and cancelled memos are immutable (Phase 10)."""
        return self.status in self.READ_ONLY_STATUSES


    @property
    def active_step(self):
        """
        The workflow step currently awaiting action, or None.

        Selected in Python over `workflow_steps.all()` rather than with a
        `.filter()` on purpose: `.filter()` ignores a prefetch cache and issues a
        fresh query every time. The list serializer asks each row for both
        `pending_with` and `ageing`, so on a 50-row page a filtered lookup cost
        100 extra queries — the viewset already prefetches
        `workflow_steps__assignee`, and this uses it.
        """
        return next(
            (step for step in self.workflow_steps.all()
             if step.status == MemoWorkflowStep.StepStatus.ACTIVE),
            None,
        )

    @property
    def has_matrix(self):
        return self.workflow_steps.exists()

    def resolved_department_name(self):
        """Department label for display: the snapshot, else the author's."""
        if self.department_name:
            return self.department_name
        if self.department_id:
            return self.department.name
        return getattr(self.created_by, "department_name", None) or ""

    def save(self, *args, **kwargs):
        # H2: a single canonical generator (memos.services.generate_memo_number)
        # is the only source of memo numbers, so every creation path (API, ORM,
        # admin) yields the same typed format NIFN-{TYPE}-{YEAR}-{SEQ}.
        if not self.memo_number:
            from . import services
            self.memo_number = services.generate_memo_number(self.memo_type)
        # Snapshot the author's department once, at creation. Guarded on "not
        # already set" so a later save never overwrites the captured value.
        if self._state.adding and self.created_by_id and not self.department_id:
            author = self.created_by
            self.department_id = getattr(author, "department_ref_id", None)
            if not self.department_name:
                self.department_name = getattr(author, "department_name", None) or ""
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.memo_number} - {self.subject}"


class MemoApprovalStep(models.Model):
    """
    Audit trail entry for a single action taken on a memo's workflow.
    """
    class Action(models.TextChoices):
        CREATED = "created", "Created"
        SUBMITTED = "submitted", "Submitted"
        SENT_FOR_REVIEW = "sent_for_review", "Sent for Review"
        REVIEWED = "reviewed", "Reviewed"
        RECOMMENDED = "recommended", "Recommended"
        SUPPORTED = "supported", "Supported"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        RETURNED = "returned", "Returned"
        ARCHIVED = "archived", "Archived"
        CANCELLED = "cancelled", "Cancelled"
        COMMENTED = "commented", "Commented"
        # Phase 49.5. The audit found every content change and every workflow
        # step landing in the shared AuditLog as a generic "Update", and none of
        # them reaching the timeline. These are memo-specific events, so they
        # belong in the memo's own history table rather than in audit.Action,
        # which is shared by every app in the project.
        EDITED = "edited", "Edited"
        NOTED = "noted", "Noted"
        ACCESS_GRANTED = "access_granted", "Archive access granted"
        ACCESS_REVOKED = "access_revoked", "Archive access revoked"
        APPROVER_CHANGED = "approver_changed", "Approver changed"
        RECOMMENDER_CHANGED = "recommender_changed", "Recommender changed"

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

    memo = models.ForeignKey(Memo, on_delete=models.CASCADE, related_name="approval_steps")
    step_order = models.PositiveIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memo_actions")
    action = models.CharField(max_length=20, choices=Action.choices)
    comment = models.TextField(blank=True, default="")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["memo", "step_order"]

    def __str__(self):
        return f"{self.memo.memo_number} - step {self.step_order} - {self.action}"


class MemoWorkflowStep(models.Model):
    """
    One row of a memo's approval matrix: who must act, in what capacity, at
    which position in the chain.

    This is deliberately a SEPARATE table from MemoApprovalStep. The two answer
    different questions and conflating them is what limited the previous design:

        MemoWorkflowStep  - forward-looking ROUTING. Rows exist before anyone
                            acts, which is what makes "waiting on X at step 3"
                            renderable and what lets the engine refuse an
                            out-of-order action.
        MemoApprovalStep  - backward-looking HISTORY. Append-only, one row per
                            action actually taken. Never rewritten.

    Exactly one step per memo is ACTIVE at a time. Advancement is sequential:
    completing the active step activates the next PENDING one by `sequence`, so
    an approver physically cannot act before the supporter ahead of them.
    """

    class RoleType(models.TextChoices):
        REVIEWER = "reviewer", "Reviewer"
        RECOMMENDER = "recommender", "Recommender"
        SUPPORTER = "supporter", "Supporter"
        APPROVER = "approver", "Approver"

    # Rank in the approval hierarchy (Phase 15):
    #
    #   Created By -> Recommended By -> Supported By -> Approved By -> Archived
    #
    # A chain's role types must never DECREASE as the sequence advances, so
    # "Recommended must complete before Supported" and "Supported before
    # Approved" are properties of the matrix itself rather than something the
    # engine has to re-check at each step. Reviewer sits at rank 0: it is not part
    # of the hierarchy above but remains a valid role type (Phase 19 still lists
    # it), so it may appear before a recommender and not after one.
    #
    # Equal ranks ARE allowed: two supporters in sequence is a legitimate chain,
    # and the sequence still forces them to act one after the other.
    ROLE_RANK = {
        "reviewer": 0,
        "recommender": 1,
        "supporter": 2,
        "approver": 3,
    }

    class StepStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Awaiting Action"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"
        SKIPPED = "skipped", "Skipped"

    # The memo status each role type produces when its step completes. Drives
    # workflow.advance() so the status ladder is data, not a chain of ifs.
    COMPLETION_STATUS = {
        RoleType.REVIEWER: "under_review",
        RoleType.RECOMMENDER: "recommended",
        RoleType.SUPPORTER: "supported",
        RoleType.APPROVER: "approved",
    }

    # Past-tense audit action recorded in MemoApprovalStep per role type.
    COMPLETION_ACTION = {
        RoleType.REVIEWER: "reviewed",
        RoleType.RECOMMENDER: "recommended",
        RoleType.SUPPORTER: "supported",
        RoleType.APPROVER: "approved",
    }

    # Notification category sent when a step becomes ACTIVE.
    ASSIGNMENT_CATEGORY = {
        RoleType.REVIEWER: "MEMO_REVIEW_REQUIRED",
        RoleType.RECOMMENDER: "MEMO_RECOMMENDATION_REQUIRED",
        RoleType.SUPPORTER: "MEMO_SUPPORT_REQUIRED",
        RoleType.APPROVER: "MEMO_APPROVAL_REQUIRED",
    }

    # Role types that must supply remarks to proceed.
    #
    # WIDENED FROM REVIEWER ALONE to every acting role (Phase
    # MEMO-WORKFLOW-FINAL-ENTERPRISE). The original set came from the manual's
    # "Reviewer (Comment Required)" (Phase 5), which named only that step —
    # so a recommender, supporter or approver could advance a memo leaving no
    # record of why. For an approval trail that is the one step whose reasoning
    # matters most, and its absence is invisible afterwards: the history shows
    # "approved" with an empty remark and nothing to say whether the approver
    # read the attachments or simply clicked through.
    #
    # THIS IS A DELIBERATE DEPARTURE FROM THE MANUAL. If p.5 is still the
    # governing document, that is the thing to change, not this line.
    COMMENT_REQUIRED_ROLES = frozenset({
        RoleType.REVIEWER, RoleType.RECOMMENDER,
        RoleType.SUPPORTER, RoleType.APPROVER,
    })


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

    memo = models.ForeignKey(Memo, on_delete=models.CASCADE, related_name="workflow_steps")
    sequence = models.PositiveIntegerField(
        help_text="1-based position in the approval chain. Contiguous and unique per memo.",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="memo_workflow_steps", db_index=True,
        help_text="SET_NULL, not CASCADE: deleting a user must never erase a row "
                  "of a completed approval matrix. The row survives with its "
                  "assignee_name snapshot intact.",
    )
    # Who this step belonged to, captured when the matrix was built.
    #
    # This exists so the assignee FK can be SET_NULL. PROTECT would keep the name
    # readable but make the user undeletable forever - and the admin console
    # genuinely needs to delete accounts. Snapshotting the name means an approval
    # record stays complete and legible after the account is gone, which is what
    # actually matters about it.
    assignee_name = models.CharField(max_length=150, blank=True, default="")
    role_type = models.CharField(max_length=20, choices=RoleType.choices)
    status = models.CharField(
        max_length=20, choices=StepStatus.choices, default=StepStatus.PENDING, db_index=True,
    )

    # Snapshots taken when the matrix is built, so the matrix table and the PDF
    # keep showing the designation/department that applied at approval time even
    # after a promotion, transfer or departure.
    designation = models.CharField(max_length=120, blank=True, default="")
    department_label = models.CharField(max_length=150, blank=True, default="")

    # When this step became the ACTIVE one. Backs the "Pending Since" and
    # "Due Days" columns in the inbox: age has to be measured from the moment the
    # step landed on this person's desk, not from the memo's creation date, or a
    # memo that spent three weeks with an earlier approver shows up as everyone's
    # overdue item.
    activated_at = models.DateTimeField(null=True, blank=True)
    acted_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["memo", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["memo", "sequence"], name="uniq_memo_step_sequence",
            ),
            # One person cannot hold two positions in the same chain - that
            # would let a single approval satisfy two separate control points.
            models.UniqueConstraint(
                fields=["memo", "assignee"], name="uniq_memo_step_assignee",
            ),
        ]
        indexes = [
            models.Index(fields=["assignee", "status"], name="memo_step_assignee_idx"),
        ]

    def __str__(self):
        return f"{self.memo_id} #{self.sequence} {self.role_type} ({self.status})"

    @property
    def requires_comment(self):
        return self.role_type in self.COMMENT_REQUIRED_ROLES

    @property
    def display_name(self):
        """
        The assignee's name for display. Reads the live user when they still
        exist, and falls back to the snapshot when the account has been deleted -
        so a historical matrix never renders a blank row.
        """
        if self.assignee_id and self.assignee is not None:
            return self.assignee.get_full_name() or self.assignee.username
        return self.assignee_name or "—"


class MemoAttachment(models.Model):
    """
    A file attached to a memo. Replaces the single Memo.attachment field for new
    memos (that field is retained so existing rows keep resolving) and is served
    through the same authenticated, forced-download endpoint.
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

    memo = models.ForeignKey(Memo, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=memo_attachment_path, max_length=255)
    original_name = models.CharField(max_length=255, blank=True, default="")
    # "File Name:" is its own box on the manual's upload modal (p.6), above
    # "Choose file" - so the uploader names the document rather than the reader
    # being shown whatever the file happened to be called on someone's desktop.
    display_name = models.CharField(
        max_length=255, blank=True, default="",
        help_text="The name the uploader gave the file. Falls back to the "
                  "original filename when left blank.")
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_attachments_uploaded",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return f"{self.label} ({self.memo_id})"

    @property
    def label(self):
        return self.display_name or self.original_name or self.file.name


class MemoSection(models.Model):
    """
    One titled block of memo content (E-memo-manual p.3).

    The manual's form opens with two of these - **Background** and
    **Recommendation** - and a "+ Add more" control described as "Click here to
    add more field (title and description)". So the body of a memo is an ordered
    list of (title, description) pairs, not one blob, and the count is up to the
    author.

    Rows rather than a JSON column: each block is sanitized on write, rendered on
    its own in the PDF, and can be added or removed individually. A JSON blob
    would make every one of those a whole-document read-modify-write.

    The two defaults are seeded on create and are ordinary rows afterwards - an
    author may retitle or delete them, which the manual's red X on a block allows.
    """
    DEFAULT_TITLES = ["Background", "Recommendation"]

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
    memo = models.ForeignKey(
        Memo, on_delete=models.CASCADE, related_name="sections")
    position = models.PositiveIntegerField(
        default=0, help_text="Render order. The list's order IS the document's.")
    title = models.CharField(max_length=150)
    body = models.TextField(blank=True, default="", help_text="Sanitized HTML.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "created_at"]
        indexes = [models.Index(fields=["memo", "position"])]

    def __str__(self):
        return f"{self.title} ({self.memo_id})"


class MemoNumberSequence(models.Model):
    """
    Per-(type_code, year) counter that hands out gap-free, race-safe memo
    sequence numbers. services.generate_memo_number() locks the matching row
    with select_for_update() and increments last_value, so two concurrent
    creations can never collide (H4), and the authoritative integer counter
    removes the old lexical-sort bug at 10000+ (5-digit seq).
    """
    type_code = models.CharField(max_length=8)
    year = models.PositiveIntegerField()
    last_value = models.PositiveIntegerField(default=0)
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

    class Meta:
        # Was (type_code, year): a shared counter, so one tenant filing a memo
        # advanced every other tenant's memo numbers.
        unique_together = ("organization", "type_code", "year")

    def __str__(self):
        return f"{self.type_code}-{self.year}: {self.last_value}"


class MemoTemplate(models.Model):
    """
    Admin-editable content template used to prefill new memos of a given type.
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

    name = models.CharField(max_length=255)
    memo_type = models.CharField(max_length=20, choices=Memo.MemoType.choices)
    subject_template = models.CharField(max_length=500)
    body_template = models.TextField()
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.memo_type})"


# ===========================================================================
# Phase 49.5 - governance gap closure
#
# Four capabilities the Phase 49 audit found missing. They share one design
# principle: NONE of them touches MemoWorkflowStep's core invariant that exactly
# one step is ACTIVE and completing it activates the next. The note round sits
# outside the sequence entirely; unavailability and transfer rewrite ONE step's
# assignee without reordering the chain; archive sharing adds readers without
# adding approvers.
# ===========================================================================
class MemoNote(models.Model):
    """
    The note round on a memo (Phase 49.5 blocker 1).

    Deliberately NOT a fifth MemoWorkflowStep.RoleType. A noted user is
    informational and must not block the workflow, and the workflow engine's
    whole contract is that the active step blocks everything behind it. Modelling
    Noted as a role would mean giving it a ROLE_RANK it has no honest place in and
    then relying on an auto-skip rule to undo the blocking - a rule that could be
    got wrong. Sitting outside the sequence makes "does not block" true by
    construction rather than by exception, which is also what the brief means by
    "display separately from approval chain".

    One row per memo, created lazily the first time anyone is asked to note.
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
    memo = models.OneToOneField(Memo, on_delete=models.CASCADE, related_name="note_round")
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_note_rounds_opened",
    )
    opened_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "memo note round"

    def __str__(self):
        return f"note round for {self.memo_id}"

    @property
    def total(self):
        return self.recipients.count()

    @property
    def completed(self):
        return self.recipients.filter(
            status=MemoNoteRecipient.NoteStatus.NOTED).count()

    @property
    def pending(self):
        return self.recipients.filter(
            status=MemoNoteRecipient.NoteStatus.PENDING).count()

    @property
    def is_complete(self):
        total = self.total
        return total > 0 and self.pending == 0


class MemoNoteRecipient(models.Model):
    """
    One person asked to note a memo, and whether they have.

    Carries the same designation/department snapshot discipline as
    MemoWorkflowStep: a noted register printed on an archived memo must keep
    reporting the designation that applied when the note was taken, and must stay
    legible after the account is deleted.
    """
    class NoteStatus(models.TextChoices):
        PENDING = "pending", "Pending Note"
        NOTED = "noted", "Noted"

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
    note_round = models.ForeignKey(
        MemoNote, on_delete=models.CASCADE, related_name="recipients")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_notes", db_index=True,
    )
    user_name = models.CharField(max_length=150, blank=True, default="")
    designation = models.CharField(max_length=120, blank=True, default="")
    department_label = models.CharField(max_length=150, blank=True, default="")

    status = models.CharField(
        max_length=12, choices=NoteStatus.choices, default=NoteStatus.PENDING,
        db_index=True)
    remarks = models.TextField(blank=True, default="")
    noted_at = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)

    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_notes_requested",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            # One request per person per memo. Asking twice would double the
            # denominator of a register people have already read.
            models.UniqueConstraint(
                fields=["note_round", "user"], name="uniq_memo_note_recipient"),
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="memo_note_user_idx"),
        ]

    def __str__(self):
        return f"{self.display_name} - {self.status}"

    @property
    def display_name(self):
        if self.user_id and self.user is not None:
            return self.user.get_full_name() or self.user.username
        return self.user_name or "—"


class MemoNoteAudit(models.Model):
    """
    Append-only log of note-round events, separate from MemoNoteRecipient's
    current state for the same reason MemoApprovalStep is separate from
    MemoWorkflowStep: state answers "where are we", history answers "what
    happened", and a row that is rewritten cannot answer the second.
    """
    class Action(models.TextChoices):
        REQUESTED = "requested", "Note requested"
        NOTED = "noted", "Noted"
        REMOVED = "removed", "Note request withdrawn"
        REMINDED = "reminded", "Note reminder sent"

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
    note_round = models.ForeignKey(
        MemoNote, on_delete=models.CASCADE, related_name="audit_entries")
    recipient = models.ForeignKey(
        MemoNoteRecipient, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=12, choices=Action.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_note_actions",
    )
    actor_name = models.CharField(max_length=150, blank=True, default="")
    subject_name = models.CharField(
        max_length=150, blank=True, default="",
        help_text="Who the action was about, snapshotted - the recipient FK is "
                  "SET_NULL and a withdrawn request deletes the recipient row.")
    remarks = models.TextField(blank=True, default="")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at"]

    def __str__(self):
        return f"{self.action} {self.subject_name} @ {self.at:%Y-%m-%d}"


class MemoStepUnavailability(models.Model):
    """
    A record that the holder of a workflow step could not act (Phase 49.5
    blocker 2).

    The memo does NOT move backwards and the chain is NOT reordered: the step
    keeps its sequence and its role, and only its assignee changes. Anything else
    would invalidate the approvals already given behind it, which is the reason
    set_matrix refuses to run on a live memo in the first place.
    """
    class Reason(models.TextChoices):
        """
        The manual's "Select Unavailable type" dropdown, verbatim (p.9).

        Five values, in the order the screenshot lists them. The previous set
        (Annual Leave / Sick Leave / Training / Meeting / Official Visit /
        Emergency) was a reasonable guess at the same idea and did not match the
        document; the stamp printed on an unavailable approver's block reads back
        the label, so a wrong label is a wrong document.
        """
        FIELD_SITE_VISIT = "field_site_visit", "Field/Site Visit"
        ON_LEAVE = "on_leave", "On Leave"
        BRANCH_VISIT = "branch_visit", "Branch Visit"
        ON_TRAINING = "on_training", "On Training"
        ON_CONFERENCE_MEETING = "on_conference_meeting", "On Conference/Meeting"

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
    memo = models.ForeignKey(
        Memo, on_delete=models.CASCADE, related_name="unavailabilities")
    step = models.ForeignKey(
        MemoWorkflowStep, on_delete=models.CASCADE, related_name="unavailabilities")
    original_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_unavailabilities",
    )
    original_assignee_name = models.CharField(max_length=150, blank=True, default="")
    role_type = models.CharField(max_length=20, blank=True, default="")

    reason = models.CharField(max_length=24, choices=Reason.choices)
    reason_note = models.TextField(blank=True, default="")

    # Who declared the absence. Either the absent person themselves or someone
    # authorised to declare it on their behalf - both are legitimate and the
    # record has to say which.
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_unavailabilities_marked",
    )
    marked_by_name = models.CharField(max_length=150, blank=True, default="")
    marked_at = models.DateTimeField(auto_now_add=True)

    # Filled when a replacement is put in place. Null means the memo is parked
    # with its owner awaiting one, which is the "Memo Returned" state.
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_unavailabilities_resolved",
        help_text="Who approved the replacement. Distinct from marked_by: "
                  "declaring an absence and authorising a stand-in are two "
                  "different acts and an audit needs both names.",
    )
    resolved_by_name = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        ordering = ["-marked_at"]
        indexes = [
            models.Index(fields=["memo", "resolved_at"], name="memo_unavail_open_idx"),
        ]

    def __str__(self):
        return f"{self.original_assignee_name} unavailable ({self.reason})"

    @property
    def is_open(self):
        return self.resolved_at is None


class MemoAssignmentTransfer(models.Model):
    """
    Append-only record of a workflow step changing hands (Phase 49.5 blockers 2
    and 3).

    One table for both flows rather than two, because the timeline, the PDF and
    the audit all ask the same question of both: which step, from whom, to whom,
    why, on whose authority. `kind` distinguishes the reasons, and
    `unavailability` links the subset that were caused by an absence.
    """
    # Labels are role-NEUTRAL on purpose: a step of any role type can be stood in
    # for, and "Acting approver" printed against a reviewer step (as it was on the
    # first generated PDF) reads as a mistake in the record.
    class Kind(models.TextChoices):
        ALTERNATE = "alternate", "Alternate"
        ACTING = "acting", "Acting"
        SELF_ASSIGN = "self_assign", "Self assigned"
        ASSIGN_NEW = "assign_new", "Reassigned"
        TRANSFER_OWNERSHIP = "transfer_ownership", "Ownership transferred"

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
    memo = models.ForeignKey(
        Memo, on_delete=models.CASCADE, related_name="assignment_transfers")
    step = models.ForeignKey(
        MemoWorkflowStep, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="transfers")
    sequence = models.PositiveIntegerField(
        default=0, help_text="The step's sequence, snapshotted so the record "
                             "survives the step row being deleted.")
    role_type = models.CharField(max_length=20, blank=True, default="")

    kind = models.CharField(max_length=20, choices=Kind.choices)
    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_steps_transferred_away",
    )
    from_user_name = models.CharField(max_length=150, blank=True, default="")
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_steps_transferred_in",
    )
    to_user_name = models.CharField(max_length=150, blank=True, default="")

    reason = models.TextField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_assignment_transfers",
    )
    actor_name = models.CharField(max_length=150, blank=True, default="")
    unavailability = models.ForeignKey(
        MemoStepUnavailability, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="transfers",
    )
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at"]

    def __str__(self):
        return f"{self.role_type} {self.from_user_name} -> {self.to_user_name}"


class MemoArchiveAccess(models.Model):
    """
    A grant of read access to an ARCHIVED memo (Phase 49.5 blocker 4).

    "Department", "Unit" and "Sub Unit" are all the same target type here, because
    leaves.Department is self-nesting via `parent` - so a unit IS a department row
    with a parent. Inventing three target kinds for one tree would have created
    three ways to express the same grant and three places for them to disagree.
    `include_children` covers the subtree instead, which is what "share with a
    department" usually means in practice.

    Grants are REVOKED, never deleted: the point of an auditable share is that
    "who could read this in March" stays answerable in June.
    """
    class Target(models.TextChoices):
        DEPARTMENT = "department", "Department or unit"
        EMPLOYEE = "employee", "Employee"
        GROUP = "group", "Employee group"

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
    memo = models.ForeignKey(
        Memo, on_delete=models.CASCADE, related_name="archive_grants")
    target = models.CharField(max_length=12, choices=Target.choices)

    department = models.ForeignKey(
        "leaves.Department", on_delete=models.CASCADE, null=True, blank=True,
        related_name="memo_archive_grants",
    )
    include_children = models.BooleanField(
        default=True,
        help_text="Departments only: also grant to every department beneath this "
                  "one in the tree.")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
        related_name="memo_archive_grants",
    )
    group = models.ForeignKey(
        "auth.Group", on_delete=models.CASCADE, null=True, blank=True,
        related_name="memo_archive_grants",
    )
    target_label = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Who was granted, snapshotted for the audit and the PDF.")

    reason = models.TextField()
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_archive_grants_made",
    )
    granted_by_name = models.CharField(max_length=150, blank=True, default="")
    granted_at = models.DateTimeField(auto_now_add=True)

    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="memo_archive_grants_revoked",
    )
    revoked_by_name = models.CharField(max_length=150, blank=True, default="")
    revoke_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-granted_at"]
        indexes = [
            models.Index(fields=["memo", "revoked_at"], name="memo_grant_live_idx"),
            models.Index(fields=["user", "revoked_at"], name="memo_grant_user_idx"),
            models.Index(fields=["department", "revoked_at"], name="memo_grant_dept_idx"),
        ]
        constraints = [
            # Exactly one target column filled, matching `target`. Without this a
            # row could name a department AND a user and the permission layer
            # would have to guess which the grantor meant.
            models.CheckConstraint(
                condition=(
                    models.Q(target="department", department__isnull=False,
                             user__isnull=True, group__isnull=True)
                    | models.Q(target="employee", user__isnull=False,
                               department__isnull=True, group__isnull=True)
                    | models.Q(target="group", group__isnull=False,
                               department__isnull=True, user__isnull=True)
                ),
                name="memo_grant_target_matches_column",
            ),
        ]

    def __str__(self):
        return f"{self.memo_id} -> {self.target_label} ({self.target})"

    @property
    def is_live(self):
        return self.revoked_at is None
