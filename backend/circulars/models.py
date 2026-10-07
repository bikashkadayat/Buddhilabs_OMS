"""
The circular data model (Phase 50).

A CIRCULAR IS NOT A MEMO AND NOT A MINUTE
-----------------------------------------
The distinction drives every design decision here, so it is worth stating once:

  A memo    asks a small, named chain of people for a DECISION. Its shape is a
            sequence: one approver at a time, and the document is blocked until
            each acts.
  A minute  RECORDS what a meeting decided, and tracks the decisions and tasks
            arising from it afterwards.
  A circular ANNOUNCES something already decided to an audience that may be the
            whole organisation. Its shape is one-to-many, and its interesting
            questions are "who received this", "who has read it" and "who has
            confirmed it" - none of which a memo ever has to answer.

So the module reuses the approval-chain machinery for the small part that IS a
chain (review, then issue) and adds a broadcast/recipient/read model for the part
that is not. What it deliberately does NOT do is treat every recipient as an
approval step: a thousand-recipient circular would be a thousand-row approval
matrix, and every "is it my turn" query in the memo engine assumes that number is
small.

THE LADDER
----------
    DRAFT
      -> UNDER_REVIEW         only when reviewers were named; skipped otherwise
      -> READY_FOR_ISSUE      review complete (or none needed)
      -> ISSUED               the issuer has signed it; an official document exists
      -> READY_FOR_BROADCAST  an audience has been defined
      -> BROADCASTED          recipients materialised and notified
      -> ARCHIVED
    REJECTED and CANCELLED sit outside the ladder.

ISSUED and READY_FOR_BROADCAST are separate states because they answer different
questions - "is this official yet" and "does it have an audience yet" - and the
transition between them is defining the audience rather than pressing a button.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from tenancy.scoping import AllTenantsManager, TenantManager


def circular_attachment_path(instance, filename):
    """
    Unguessable per-file directory, matching the memo and minute convention. Even
    if MEDIA were ever served directly the path cannot be guessed; downloads still
    go through a signed, expiring URL.
    """
    # Phase S3: tenant-scoped, via the one shared standard in
    # tenancy.storage. The function NAME and LOCATION are unchanged, so
    # Django's serialised `upload_to` import path still resolves and NO
    # migration is needed. Files already stored keep their old paths and
    # still resolve -- the media view looks a file up by its stored name.
    from tenancy.storage import upload_path

    return upload_path(instance, filename, module="circulars",
                       kind="attachments")


class Circular(models.Model):
    """One circular: the document itself, and where it is in its lifecycle."""

    class Category(models.TextChoices):
        """
        What kind of communication this is - the six purposes the brief names.

        A category, not a "type", because it carries no workflow meaning: an HR
        announcement and a policy circular travel the same road. It exists for
        filtering and for the reader's orientation.
        """
        POLICY = "policy", "Policy Communication"
        ADMINISTRATIVE = "administrative", "Administrative Notice"
        HR = "hr", "HR Announcement"
        MANAGEMENT = "management", "Management Communication"
        GOVERNANCE = "governance", "Governance Update"
        INSTRUCTION = "instruction", "Official Instruction"
        # Phase BOD-ROLE-EXECUTIVE-GOVERNANCE. Always reaches every Board member,
        # whatever else its audience - see broadcast.resolve_audience.
        EXECUTIVE = "executive", "Executive Circular"

    class Classification(models.TextChoices):
        """
        How widely the circular may travel, mirroring the memo module's vocabulary
        so the two do not teach the reader two different words for one idea.

        INTERNAL is the default rather than NORMAL: every circular is internal by
        nature, and the meaningful question is whether it is narrower than that.
        """
        INTERNAL = "internal", "Internal"
        CONFIDENTIAL = "confidential", "Confidential"
        RESTRICTED = "restricted", "Restricted"
        PUBLIC = "public", "Public"

    # Classifications that mean "only the audience, the chain and HR/Admin".
    # PUBLIC and INTERNAL do not restrict; a circular's audience already bounds it.
    RESTRICTED_CLASSIFICATIONS = frozenset({"confidential", "restricted"})

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UNDER_REVIEW = "under_review", "Under Review"
        READY_FOR_ISSUE = "ready_for_issue", "Ready For Issue"
        ISSUED = "issued", "Issued"
        READY_FOR_BROADCAST = "ready_for_broadcast", "Ready For Broadcast"
        BROADCASTED = "broadcasted", "Broadcasted"
        ARCHIVED = "archived", "Archived"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    # Editable only from these. Once a circular has been issued its content is the
    # official text and must not change under the people who signed it.
    EDITABLE_STATUSES = frozenset({Status.DRAFT, Status.REJECTED})
    # Read-only everywhere: serializer flags, the update guard and the engine all
    # consult this one set.
    READ_ONLY_STATUSES = frozenset({Status.ARCHIVED, Status.CANCELLED})
    # The circular exists as an official document from here on.
    OFFICIAL_STATUSES = frozenset({
        Status.ISSUED, Status.READY_FOR_BROADCAST, Status.BROADCASTED,
        Status.ARCHIVED,
    })
    # Statuses where the workflow is over, however it ended.
    TERMINAL_STATUSES = frozenset({Status.ARCHIVED, Status.CANCELLED,
                                   Status.REJECTED})

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
    circular_number = models.CharField(max_length=30, editable=False)

    subject = models.CharField(max_length=500)
    content = models.TextField(
        help_text="The circular body, as sanitized HTML. Sanitized on write, on "
                  "read and again when the PDF renders.")

    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.ADMINISTRATIVE,
        db_index=True)
    classification = models.CharField(
        max_length=20, choices=Classification.choices,
        default=Classification.INTERNAL, db_index=True)
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True)

    # The date the circular bears, which is not the date it was drafted and not the
    # date it was broadcast. A circular issued on the 1st and broadcast on the 3rd
    # is still "the circular of the 1st", and the PDF prints this one.
    issue_date = models.DateField(
        null=True, blank=True,
        help_text="The date the circular bears. Distinct from created_at and from "
                  "the broadcast date.")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="circulars_created", db_index=True)

    # Department snapshot, taken at creation from the author. Snapshotted rather
    # than followed through created_by so a transfer or a renamed unit never
    # silently reassigns a historical circular.
    department = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="circulars", db_index=True)
    department_name = models.CharField(max_length=150, blank=True, default="")

    # --- provenance (the brief's Memo Reference / Minute Reference) -----------
    #
    # Real foreign keys, not free text: a circular that announces what a memo
    # approved or a minute resolved should be able to take the reader there. The
    # minute module's own audit found the opposite choice - free-text only - and
    # the resulting dead end.
    memo_reference = models.ForeignKey(
        "memos.Memo", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="circulars",
        help_text="The memo whose decision this circular announces.")
    minute_reference = models.ForeignKey(
        "minutes.Minute", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="circulars",
        help_text="The minute whose resolution this circular announces.")
    external_reference = models.CharField(
        max_length=100, blank=True, default="", db_index=True,
        help_text="A reference outside the system - a board decision, a "
                  "regulator's directive, an incoming letter.")

    # --- acknowledgement configuration ---------------------------------------
    #
    # The brief makes this explicitly configurable per circular. Most circulars are
    # information; some need a recorded confirmation that each person read them.
    acknowledgement_required = models.BooleanField(
        default=False,
        help_text="When set, every recipient is asked to confirm or decline. "
                  "Independent of read tracking, which happens either way.")
    acknowledgement_due_days = models.PositiveSmallIntegerField(
        default=7,
        help_text="Days from broadcast within which an acknowledgement is expected.")

    # --- lifecycle stamps ----------------------------------------------------
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="circulars_issued")
    issued_by_name = models.CharField(max_length=150, blank=True, default="")
    broadcast_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_by"], name="cir_status_author_idx"),
            models.Index(fields=["status", "department"], name="cir_status_dept_idx"),
            models.Index(fields=["-created_at"], name="cir_created_desc_idx"),
            models.Index(fields=["classification"], name="cir_classification_idx"),
        ]
        # Phase S4: was unique=True on `circular_number` alone -- a GLOBAL namespace.
        # Phase S3 already gives each tenant its own document prefix, so a
        # collision was impossible in practice; the composite makes it
        # impossible by construction and removes the last way one tenant's
        # numbering could refuse another tenant's insert.
        constraints = [
            models.UniqueConstraint(fields=["organization", "circular_number"],
                                    name="uniq_circular_number_org"),
        ]

    def __str__(self):
        return f"{self.circular_number} - {self.subject}"

    def save(self, *args, **kwargs):
        if not self.circular_number:
            from . import services
            self.circular_number = services.generate_circular_number()
        if self._state.adding and self.created_by_id and not self.department_id:
            author = self.created_by
            self.department_id = getattr(author, "department_ref_id", None)
            if not self.department_name:
                self.department_name = (
                    getattr(author, "department_name", None)
                    or getattr(author, "department", "") or "")[:150]
        super().save(*args, **kwargs)

    # --- derived state -------------------------------------------------------
    @property
    def is_read_only(self):
        return self.status in self.READ_ONLY_STATUSES

    @property
    def is_editable(self):
        return self.status in self.EDITABLE_STATUSES

    @property
    def is_official(self):
        """True once issued: the content is the official text from this point."""
        return self.status in self.OFFICIAL_STATUSES

    @property
    def is_restricted(self):
        return self.classification in self.RESTRICTED_CLASSIFICATIONS

    @property
    def active_step(self):
        """
        The workflow step awaiting action, or None.

        Selected in Python over `workflow_steps.all()` rather than with `.filter()`,
        because `.filter()` ignores a prefetch cache and fires a fresh query per
        row - the mistake that once cost the memo list a hundred queries a page.
        """
        return next(
            (step for step in self.workflow_steps.all()
             if step.status == CircularWorkflowStep.StepStatus.ACTIVE), None)

    @property
    def latest_broadcast(self):
        return next(iter(self.broadcasts.all()), None)

    @property
    def acknowledgement_deadline(self):
        """
        The date acknowledgements are due, or None if the round has not opened.

        Derived from the broadcast timestamp rather than stored, so it is correct
        the moment the due-days setting changes and cannot drift from it.
        """
        if self.broadcast_at is None or not self.acknowledgement_required:
            return None
        import datetime
        return (timezone.localtime(self.broadcast_at).date()
                + datetime.timedelta(days=self.acknowledgement_due_days))

    def resolved_department_name(self):
        if self.department_name:
            return self.department_name
        if self.department_id:
            return self.department.name
        return getattr(self.created_by, "department_name", None) or ""


class CircularWorkflowStep(models.Model):
    """
    One step of the pre-issue chain: a reviewer, or the issuer.

    Only TWO role types, and that is the point. A circular is not approved by a
    hierarchy - it is checked (optionally, by any number of reviewers) and then
    issued (by exactly one person with the authority to issue it). Modelling it
    with the memo's four ranked roles would invite a recommender/supporter chain
    that a circular has no use for.

    Same discipline as the memo module: assignee is SET_NULL with a name snapshot,
    so a completed chain stays legible after an account is deleted.
    """

    class RoleType(models.TextChoices):
        REVIEWER = "reviewer", "Reviewer"
        ISSUER = "issuer", "Issuer"

    class StepStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Awaiting Action"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"
        SKIPPED = "skipped", "Skipped"

    COMPLETION_STATUS = {
        RoleType.REVIEWER: Circular.Status.READY_FOR_ISSUE,
        RoleType.ISSUER: Circular.Status.ISSUED,
    }
    ASSIGNMENT_CATEGORY = {
        RoleType.REVIEWER: "CIRCULAR_REVIEW_REQUIRED",
        RoleType.ISSUER: "CIRCULAR_ISSUE_REQUIRED",
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
    circular = models.ForeignKey(
        Circular, on_delete=models.CASCADE, related_name="workflow_steps")
    sequence = models.PositiveIntegerField(
        help_text="1-based position. Contiguous and unique per circular.")
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="circular_workflow_steps", db_index=True)
    assignee_name = models.CharField(max_length=150, blank=True, default="")
    designation = models.CharField(max_length=120, blank=True, default="")
    department_label = models.CharField(max_length=150, blank=True, default="")

    role_type = models.CharField(max_length=12, choices=RoleType.choices)
    status = models.CharField(
        max_length=12, choices=StepStatus.choices, default=StepStatus.PENDING,
        db_index=True)

    activated_at = models.DateTimeField(null=True, blank=True)
    acted_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["circular", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["circular", "sequence"], name="uniq_cir_step_sequence"),
            # One person cannot hold two positions in one chain: a single sign-off
            # would otherwise satisfy two separate control points.
            models.UniqueConstraint(
                fields=["circular", "assignee"], name="uniq_cir_step_assignee"),
        ]
        indexes = [
            models.Index(fields=["assignee", "status"], name="cir_step_assignee_idx"),
        ]

    def __str__(self):
        return f"{self.circular_id} #{self.sequence} {self.role_type} ({self.status})"

    @property
    def display_name(self):
        if self.assignee_id and self.assignee is not None:
            return self.assignee.get_full_name() or self.assignee.username
        return self.assignee_name or "—"


class CircularBroadcast(models.Model):
    """
    One broadcast event: who sent it, when, to whom, and how many that turned out
    to be.

    A circular can be broadcast more than once - an amended circular, or one
    extended to a department that was missed - so this is a log rather than a set
    of columns on Circular. Each row keeps the audience SPECIFICATION alongside
    the resolved count, because "sent to the whole organisation" and "sent to 412
    people" are different facts and a reader six months later needs both: the
    headcount will have changed.
    """

    class Audience(models.TextChoices):
        ORGANISATION = "organisation", "Entire Organisation"
        DEPARTMENTS = "departments", "Selected Departments"
        GROUPS = "groups", "Employee Groups"
        EMPLOYEES = "employees", "Selected Employees"
        BOARD = "board", "Board of Directors"

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
    circular = models.ForeignKey(
        Circular, on_delete=models.CASCADE, related_name="broadcasts")
    sequence = models.PositiveIntegerField(
        default=1, help_text="1 for the original broadcast, 2+ for extensions.")

    audience = models.CharField(max_length=14, choices=Audience.choices)
    departments = models.ManyToManyField(
        "leaves.Department", blank=True, related_name="circular_broadcasts")
    groups = models.ManyToManyField(
        "auth.Group", blank=True, related_name="circular_broadcasts")
    employees = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        related_name="circular_broadcasts_targeted")
    include_child_departments = models.BooleanField(
        default=True,
        help_text="Departments only: also reach every department beneath the named "
                  "ones. leaves.Department is self-nesting, so a unit IS a "
                  "department row with a parent.")

    audience_label = models.CharField(
        max_length=300, blank=True, default="",
        help_text="Human description of the audience, snapshotted - so the record "
                  "still reads correctly after a department is renamed.")
    recipient_count = models.PositiveIntegerField(default=0)
    # Recipients who were already on the circular from an earlier broadcast and so
    # were not added again. Reported rather than hidden: "extended to 40 people, 12
    # of whom already had it" is the honest answer.
    already_present_count = models.PositiveIntegerField(default=0)

    broadcast_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="circular_broadcasts_sent")
    broadcast_by_name = models.CharField(max_length=150, blank=True, default="")
    broadcast_at = models.DateTimeField(auto_now_add=True)
    remarks = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-broadcast_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["circular", "sequence"], name="uniq_cir_broadcast_sequence"),
        ]

    def __str__(self):
        return f"{self.circular_id} broadcast #{self.sequence} ({self.recipient_count})"


class CircularRecipient(models.Model):
    """
    One person the circular was broadcast to. The spine of the audience model.

    Read state and acknowledgement hang off this row rather than living in it,
    because they answer different questions and one of them is optional: read
    tracking always happens, acknowledgement only when the circular asks for it.
    Keeping them separate means an information-only circular carries no empty
    acknowledgement rows at all.

    A person appears at most ONCE per circular however many broadcasts reached
    them - enforced by a constraint, because a duplicate would double the
    denominator of every percentage on the dashboard.
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
    circular = models.ForeignKey(
        Circular, on_delete=models.CASCADE, related_name="recipients")
    broadcast = models.ForeignKey(
        CircularBroadcast, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="recipients",
        help_text="Which broadcast first reached this person. SET_NULL so deleting "
                  "a broadcast log never removes somebody from the audience.")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="circular_recipients", db_index=True)

    # Snapshots, so a distribution list printed on an archived circular keeps
    # reporting the department and designation that applied at the time.
    user_name = models.CharField(max_length=150, blank=True, default="")
    designation = models.CharField(max_length=120, blank=True, default="")
    department_label = models.CharField(max_length=150, blank=True, default="")

    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["circular", "user"], name="uniq_cir_recipient"),
        ]
        indexes = [
            models.Index(fields=["user", "circular"], name="cir_recipient_user_idx"),
        ]

    def __str__(self):
        return f"{self.user_name} <- {self.circular_id}"

    @property
    def display_name(self):
        if self.user_id and self.user is not None:
            return self.user.get_full_name() or self.user.username
        return self.user_name or "—"


class CircularReadLog(models.Model):
    """
    Whether a recipient has opened the circular, and when they last looked.

    ONE row per recipient with a first/last stamp and a counter - not one row per
    view. A row per view would be an analytics feature nobody asked for, and it
    would grow without bound on a circular the whole organisation keeps reopening.
    The brief asks for OpenedAt and LastViewedAt, which is exactly first and last.

    Absence of a row IS "unread". That is deliberate: it means the unread count
    needs no backfill for a circular broadcast before this table existed, and no
    row is written for somebody who never opens it.
    """
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
    recipient = models.OneToOneField(
        CircularRecipient, on_delete=models.CASCADE, related_name="read_log")
    opened_at = models.DateTimeField(
        help_text="First open. Never updated afterwards.")
    last_viewed_at = models.DateTimeField()
    view_count = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-last_viewed_at"]

    def __str__(self):
        return f"read by {self.recipient_id} x{self.view_count}"


class CircularAcknowledgement(models.Model):
    """
    A recipient's confirmation - or refusal - that they have read the circular.

    Only exists when the circular asks for one. A DECLINE is a first-class outcome
    rather than an error: somebody may legitimately say "I have read this and I do
    not accept it", and a governance system that only records agreement is
    recording less than it claims.

    Distinct from CircularReadLog: opening a document is a fact the system
    observes, confirming it is a statement the person makes. Conflating the two
    would let a page view stand in for a signature.
    """

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        DECLINED = "declined", "Declined"

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
    recipient = models.OneToOneField(
        CircularRecipient, on_delete=models.CASCADE,
        related_name="acknowledgement")
    state = models.CharField(
        max_length=14, choices=State.choices, default=State.PENDING, db_index=True)
    remarks = models.TextField(blank=True, default="")
    responded_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateField(
        null=True, blank=True,
        help_text="Snapshotted at broadcast, so a later change to the circular's "
                  "due-days setting cannot move a deadline somebody was already "
                  "given.")
    reminded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["recipient__user_name"]

    def __str__(self):
        return f"{self.recipient_id}: {self.state}"

    @property
    def is_late(self):
        """
        Responded after the deadline, or still outstanding past it.

        Derived, never stored, so it is correct the instant a deadline passes -
        the same rule the minute module's action items follow.
        """
        if self.due_date is None:
            return False
        if self.responded_at is not None:
            return timezone.localtime(self.responded_at).date() > self.due_date
        return timezone.localdate() > self.due_date


class CircularAttachment(models.Model):
    """
    A file issued with the circular.

    No versioning, unlike the minute module's attachments - and deliberately so. A
    minute's attachment is evidence that may be superseded while the record stands;
    a circular's attachment is part of the announcement. Correcting one means
    issuing an amended circular, which is a governance act rather than a file
    replacement.
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
    circular = models.ForeignKey(
        Circular, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=circular_attachment_path, max_length=255)
    original_name = models.CharField(max_length=255, blank=True, default="")
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="circular_attachments_uploaded")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return f"{self.original_name or self.file.name} ({self.circular_id})"

    @property
    def extension(self):
        name = self.original_name or self.file.name
        return name.rsplit(".", 1)[-1].lower() if "." in name else ""


class CircularAuditLog(models.Model):
    """
    Append-only history of everything that happened to a circular.

    The module's own log, not just `audit.AuditLog`, for the same reason the memo
    module keeps MemoApprovalStep: the shared audit table has eight generic actions
    (create/update/delete/approve/...) and cannot express "broadcast to 412 people"
    or "acknowledgement declined". Both are written - this one drives the timeline
    a user reads, the shared one carries the IP address and user agent for
    forensics.
    """

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        EDITED = "edited", "Edited"
        SENT_FOR_REVIEW = "sent_for_review", "Sent for Review"
        REVIEWED = "reviewed", "Reviewed"
        REJECTED = "rejected", "Returned for revision"
        ISSUED = "issued", "Issued"
        AUDIENCE_SET = "audience_set", "Audience defined"
        BROADCAST = "broadcast", "Broadcast"
        READ = "read", "Opened by a recipient"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        DECLINED = "declined", "Acknowledgement declined"
        REMINDED = "reminded", "Acknowledgement reminder sent"
        ARCHIVED = "archived", "Archived"
        CANCELLED = "cancelled", "Cancelled"
        ATTACHED = "attached", "Attachment added"
        ATTACHMENT_REMOVED = "attachment_removed", "Attachment removed"
        IMPORTED = "imported", "Content imported"
        EXPORTED = "exported", "Exported"

    # Actions written once per RECIPIENT rather than once per circular. Excluded
    # from the timeline a user reads, or a 400-recipient circular's timeline would
    # be 400 "opened by" rows deep and the workflow events would be unfindable.
    HIGH_VOLUME_ACTIONS = frozenset({Action.READ})

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
    circular = models.ForeignKey(
        Circular, on_delete=models.CASCADE, related_name="audit_entries")
    sequence = models.PositiveIntegerField(default=0)
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="circular_actions")
    actor_name = models.CharField(max_length=150, blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "sequence"]
        indexes = [
            models.Index(fields=["circular", "action"], name="cir_audit_action_idx"),
        ]

    def __str__(self):
        return f"{self.circular_id} {self.action} @ {self.at:%Y-%m-%d %H:%M}"


class CircularNumberSequence(models.Model):
    """
    Per-year counter handing out gap-free, race-safe circular numbers.

    services.generate_circular_number() locks the row with select_for_update() and
    increments, so two concurrent creations serialise on the lock instead of racing
    on a "max existing number" read. The counter is an integer, so the sequence
    widens past 999999 rather than colliding or sorting wrongly.
    """
    year = models.PositiveIntegerField()
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
        # Was unique=True on `year`: a platform-wide shared counter.
        constraints = [
            models.UniqueConstraint(fields=["organization", "year"],
                                    name="uniq_circular_sequence_org_year"),
        ]
    last_value = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"CIR-{self.year}: {self.last_value}"
