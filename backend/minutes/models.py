"""
The Minute module's data model.

THE E-MINUTE MANUAL IS THE SPECIFICATION
----------------------------------------
This module implements the workflow in `E-minute-manual.pdf`, which is deliberately
short:

    Draft ──Submit for Draft Review──▶ Draft For Review (with the FRO)
      │                                          │
      └──────────Submit for Acknowledge──────────┘
                            │
                            ▼
      assigned to the MEMBERS PRESENT ──all acknowledged──▶ Archived

There is no approver. Earlier revisions of this module ran minutes through the memo
module's four-role rank-ordered approval chain (reviewer → recommender → supporter →
approver, with Recommended/Supported/Approved statuses). The manual describes nothing
of the sort: a draft minute offers exactly two buttons, "Submit for Draft Review" and
"Submit for Acknowledge" (p.7), and the terminal event is "once a minute is
acknowledged by all present members, the minute will be archived" (p.10). The chain,
the decision register, the action-item register, the resolution numbering and the
traceability map have all been retired for that reason - the document, not the previous
implementation, is the authority.

The one documented step deliberately NOT implemented is the OTP challenge before
acknowledging (p.9). Acknowledgement is recorded directly, and everything else about it
- who is asked, who has answered, the signature blocks, the audit row - is unchanged.

WHY TAXONOMY IS IN THE DATABASE AND STATE IS NOT
------------------------------------------------
Minute types (DEPARTMENT / BRANCH / MANCOM / OTHERS, p.4) are rows: an organisation
adds one without a deployment. Statuses stay as TextChoices, because they are not
configuration - they are the states this module's own code branches on. A status the
engine has never heard of cannot be given behaviour by inserting a row.

PEOPLE ARE ALWAYS SET_NULL PLUS A SNAPSHOT
------------------------------------------
Every reference to a user in anything historical (a participant, an acknowledgement, an
audit row) is `on_delete=SET_NULL` alongside a text snapshot of the name. PROTECT made
employee accounts undeletable the moment they appeared in any workflow; CASCADE would
silently delete history. The snapshot means a deleted employee still reads correctly on
a five-year-old minute.
"""
import datetime
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from tenancy.scoping import AllTenantsManager, TenantManager


# ---------------------------------------------------------------------------
# Taxonomy (database-driven, seeded by migration 0002)
# ---------------------------------------------------------------------------
def minute_attachment_path(instance, filename):
    """``org/<id>/minutes/attachments/<YYYY>/<MM>/<uuid>/<file>`` (Phase S3).

    Was the literal string "minutes/attachments/%Y/%m/" -- no tenant, and the
    original filename kept verbatim, so the path was guessable. The other
    document modules already used an unguessable directory; this one now
    matches them.
    """
    from tenancy.storage import upload_path

    return upload_path(instance, filename, module="minutes",
                       kind="attachments")


class TaxonomyBase(models.Model):
    """Shared shape for the lookup tables: code, label, ordering, on/off.

    `code` is NOT unique here (Phase S2). Uniqueness is per-organization and is
    declared on the concrete subclass, which is where the `organization` column
    lives -- an abstract base cannot carry a constraint that references a field
    its subclasses define.
    """
    code = models.SlugField(max_length=40)
    name = models.CharField(max_length=120)
    ordering = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ["ordering", "name"]

    def __str__(self):
        return self.name


class MinuteType(TaxonomyBase):
    """The Minute Type dropdown (p.4): DEPARTMENT, BRANCH, MANCOM, OTHERS, …"""

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

    class Meta(TaxonomyBase.Meta):
        constraints = [
            # Was unique=True on TaxonomyBase.code: two tenants could not both
            # have a "DEPARTMENT" minute type.
            models.UniqueConstraint(fields=["organization", "code"],
                                    name="uniq_minute_type_org_code"),
        ]


class MinuteNumberSequence(models.Model):
    """
    Authoritative per-year counter behind MIN-YYYY-000001.

    A counter row locked with select_for_update(), not a "max existing number" read:
    concurrent creations serialise on the lock instead of racing, and an integer
    counter cannot develop the lexical-sort bug a zero-padded string does once it
    passes its padding width.
    """
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
        verbose_name = "minute number sequence"
        # Was unique=True on `year`: one counter row for the whole platform, so
        # one tenant recording a minute advanced everybody else's numbering.
        constraints = [
            models.UniqueConstraint(fields=["organization", "year"],
                                    name="uniq_minute_sequence_org_year"),
        ]

    def __str__(self):
        return f"{self.year}: {self.last_value}"


# ---------------------------------------------------------------------------
# The minute
# ---------------------------------------------------------------------------
class Minute(models.Model):
    """
    One meeting minute, carrying exactly the fields the manual's form carries (p.4).

    `subject` is optional because the manual's create form has none - the minute is
    identified in every list by its type and meeting date (pp. 6, 10). It is kept as an
    optional field because the archive's "Search By" offers Subject (p.10) and a
    human-readable label makes the lists usable.
    """
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        DRAFT_FOR_REVIEW = "draft_for_review", "Draft For Review"
        PENDING_ACKNOWLEDGEMENT = "pending_acknowledgement", "Pending Acknowledgement"
        ARCHIVED = "archived", "Archived"

    # Once every present member has acknowledged, the record is permanent (p.10).
    READ_ONLY_STATUSES = frozenset({Status.ARCHIVED})
    # The manual lets the initiator edit a draft and a draft under review (pp. 6, 7).
    EDITABLE_STATUSES = frozenset({Status.DRAFT, Status.DRAFT_FOR_REVIEW})

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
    minute_number = models.CharField(max_length=32, db_index=True)

    # "Reference No: the previous meeting minute reference number" (p.5). Searching it
    # copies that minute's agenda into this one's body; the value is kept so the
    # archive list can show the Reference No. column (p.10).
    reference_number = models.CharField(
        max_length=64, blank=True,
        help_text="The previous meeting's minute reference, if this minute cites one.")

    subject = models.CharField(
        max_length=255, blank=True,
        help_text="Optional. The manual's form has no subject; lists identify a "
                  "minute by type and meeting date.")

    # --- the form (p.4) ---
    minute_type = models.ForeignKey(
        MinuteType, on_delete=models.PROTECT, related_name="minutes",
        help_text="DEPARTMENT / BRANCH / MANCOM / OTHERS.")
    meeting_date = models.DateField(
        default=timezone.localdate, help_text="Date* - the date of the meeting.")
    meeting_time = models.TimeField(help_text="Time* - the meeting start time.")

    # "FRO: First reporting officer to access the meeting minute (if required)" (p.4).
    # This is who a draft goes to for review, which is what makes "Submit for Draft
    # Review" (p.7) have a destination.
    fro = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="minutes_as_fro",
        help_text="First reporting officer. Receives the minute on Submit for Draft "
                  "Review, and may access it throughout.")
    fro_name = models.CharField(max_length=150, blank=True)

    # "Meeting Agenda/Discussion/Decisions" - one rich-text body holding the manual's
    # S.N. / Agendas / Responsibility (Primary, Secondary) / Deadline table (p.4).
    # A rich-text table rather than child rows because that is what the manual shows,
    # and because a secretary types a minute as prose with a table in it.
    agenda_body = models.TextField(
        blank=True,
        help_text="Sanitized HTML. The agenda, discussion and decisions table.")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="minutes_initiated",
        help_text="The initiator - 'Initiated By' in the archive list.")
    department = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="minutes")
    department_name = models.CharField(
        max_length=120, blank=True,
        help_text="Snapshot, so a renamed department does not rewrite history on an "
                  "archived minute.")

    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.DRAFT, db_index=True)

    acknowledgement_due_days = models.PositiveSmallIntegerField(
        default=7,
        help_text="Days present members have to acknowledge once the round opens.")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_for_review_at = models.DateTimeField(null=True, blank=True)
    acknowledgement_opened_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["created_by", "status"]),
            models.Index(fields=["minute_type", "-meeting_date"]),
        ]
        # Phase S4: was unique=True on `minute_number` alone -- a GLOBAL namespace.
        # Phase S3 already gives each tenant its own document prefix, so a
        # collision was impossible in practice; the composite makes it
        # impossible by construction and removes the last way one tenant's
        # numbering could refuse another tenant's insert.
        constraints = [
            models.UniqueConstraint(fields=["organization", "minute_number"],
                                    name="uniq_minute_number_org"),
        ]

    def __str__(self):
        return f"{self.minute_number} - {self.display_title}"

    @property
    def display_title(self):
        """What the lists show. The subject if there is one, else type and date."""
        if self.subject:
            return self.subject
        return f"{self.minute_type.name if self.minute_type_id else 'Minute'} " \
               f"{self.meeting_date:%Y-%m-%d}"

    @property
    def is_read_only(self):
        return self.status in self.READ_ONLY_STATUSES

    @property
    def is_editable(self):
        return self.status in self.EDITABLE_STATUSES

    # --- participant groupings, read straight off the prefetched rows ---
    def _by_attendance(self, attendance):
        return [p for p in self.participants.all() if p.attendance == attendance]

    @property
    def members_present(self):
        return self._by_attendance(MinuteParticipant.Attendance.PRESENT)

    @property
    def members_absent(self):
        return self._by_attendance(MinuteParticipant.Attendance.ABSENT)

    @property
    def invitee_members(self):
        return self._by_attendance(MinuteParticipant.Attendance.INVITEE)

    @property
    def acknowledgement_deadline(self):
        """
        The date every present member must have acknowledged by, derived from when the
        round opened rather than stored - so changing `acknowledgement_due_days` moves
        the deadline instead of leaving a stale copy behind.
        """
        if not self.acknowledgement_opened_at:
            return None
        return (self.acknowledgement_opened_at
                + datetime.timedelta(days=self.acknowledgement_due_days)).date()

    @property
    def acknowledgement_is_complete(self):
        """
        True when every member PRESENT has acknowledged - the condition the manual
        gives for archiving (p.10).

        A minute with no present members can never satisfy it, so it returns False
        rather than vacuously True; the workflow refuses to open a round in that case
        instead of archiving something nobody confirmed.
        """
        present = self.members_present
        if not present:
            return False
        return all(p.ack_status == MinuteParticipant.AckStatus.ACKNOWLEDGED
                   for p in present)


class MinuteParticipant(models.Model):
    """
    One person on the minute, in one of the manual's three groups (p.4): Members
    Present, Members Absent, Invitee Members.

    Attendance IS the grouping here, because that is how the form asks for it - three
    pickers, one per group - rather than a role with a separate attendance column.

    Only members PRESENT are asked to acknowledge: "the minute will be assigned to the
    members present in the meeting" (p.8). Absent members appear on the signature
    sheet stamped ABSENT (p.9), and invitees appear without being asked to confirm.
    """
    class Attendance(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        INVITEE = "invitee", "Invitee"

    class AckStatus(models.TextChoices):
        NOT_REQUIRED = "not_required", "Not Required"
        PENDING = "pending", "Pending Acknowledgement"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"

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
    minute = models.ForeignKey(
        Minute, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="minute_participations")
    user_name = models.CharField(max_length=150, blank=True)
    designation = models.CharField(max_length=120, blank=True)
    department_label = models.CharField(max_length=120, blank=True)

    attendance = models.CharField(
        max_length=12, choices=Attendance.choices, default=Attendance.PRESENT,
        db_index=True)
    ack_status = models.CharField(
        max_length=16, choices=AckStatus.choices, default=AckStatus.NOT_REQUIRED,
        db_index=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["attendance", "user_name"]
        unique_together = [("minute", "user")]
        indexes = [models.Index(fields=["user", "ack_status"])]

    def __str__(self):
        return f"{self.display_name} ({self.attendance})"

    @property
    def display_name(self):
        if self.user_id and self.user:
            return self.user.get_full_name() or self.user.username
        return self.user_name or "—"

    @property
    def must_acknowledge(self):
        return self.attendance == self.Attendance.PRESENT

    @property
    def ack_due_date(self):
        """The deadline, derived from when the round opened - never stored twice."""
        opened = self.minute.acknowledgement_opened_at
        if not opened or not self.must_acknowledge:
            return None
        return (opened + datetime.timedelta(
            days=self.minute.acknowledgement_due_days)).date()

    @property
    def is_late(self):
        due = self.ack_due_date
        if due is None or self.ack_status == self.AckStatus.ACKNOWLEDGED:
            return False
        return timezone.localdate() > due


class MinuteInvolvement(models.Model):
    """
    A stakeholder routed the minute without being a participant - what fills the
    manual's "Assigned Minute (Involvement)" menu (p.3).
    """
    class AssignmentType(models.TextChoices):
        ACTION_REQUIRED = "action_required", "Action Required"
        REVIEW_REQUIRED = "review_required", "Review Required"
        INFORMATION_ONLY = "information_only", "Information Only"

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
    minute = models.ForeignKey(
        Minute, on_delete=models.CASCADE, related_name="involvements")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="minute_involvements")
    assignment_type = models.CharField(
        max_length=20, choices=AssignmentType.choices,
        default=AssignmentType.INFORMATION_ONLY, db_index=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    acted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["assignment_type", "created_at"]
        unique_together = [("minute", "user")]
        indexes = [models.Index(fields=["user", "assignment_type"])]

    def __str__(self):
        return f"{self.minute.minute_number} {self.user_id} {self.assignment_type}"


class MinuteAttachment(models.Model):
    """
    A file held against the minute, with version history.

    VERSIONS ARE ROWS, NOT AN OVERWRITE. Replacing a document creates a new row that
    points at the one it supersedes; the old row keeps its file. That matters for a
    governance record: "the meeting saw version 2 of the budget" is only meaningful if
    version 1 still exists. `is_current` marks the head of each chain so the normal
    listing shows one row per document rather than every revision ever uploaded.

    Files are served ONLY through signed, expiring, user-bound URLs
    (documents.protected_media). There is no public /media/ route in this project.
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
    minute = models.ForeignKey(
        Minute, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=minute_attachment_path, max_length=255)
    original_name = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField(default=0)

    version = models.PositiveIntegerField(default=1)
    replaces = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="superseded_by",
        help_text="The version this one replaces. SET_NULL so deleting an old version "
                  "does not cascade away the current one.")
    is_current = models.BooleanField(
        default=True, db_index=True,
        help_text="False once a newer version supersedes this row.")

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="minute_attachments")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]
        indexes = [models.Index(fields=["minute", "is_current"])]

    def __str__(self):
        return f"{self.original_name or self.file} v{self.version}"

    @property
    def extension(self):
        name = self.original_name or self.file.name or ""
        return name.rsplit(".", 1)[-1].lower() if "." in name else ""

    @property
    def is_previewable(self):
        """
        Whether a browser can render it inline. PDFs and images can; Office documents
        cannot without a converter, so offering "preview" for a .docx would be a button
        that downloads it and pretends otherwise.
        """
        return self.extension in {"pdf", "png", "jpg", "jpeg", "gif", "webp"}

    @property
    def size_label(self):
        size = self.size or 0
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size / (1024 * 1024):.1f} MB"


class MinuteAuditLog(models.Model):
    """
    The minute's own immutable trail: who, what, when, from where, and what they said.

    A minute-scoped table IN ADDITION TO the global audit.AuditLog, not instead of it.
    The global log is a generic-FK table covering every module; this one is what the
    minute's audit panel and Excel export read, and it can be indexed per minute
    without scanning an application-wide table. Both are written in the same
    transaction as the state change they describe.

    Rows are never updated. There is no `updated_at` and nothing writes to an existing
    row.
    """
    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Edited"
        SENT_FOR_REVIEW = "sent_for_review", "Submitted for draft review"
        REVIEW_RETURNED = "review_returned", "Returned by reviewer"
        SENT_FOR_ACK = "sent_for_ack", "Submitted for acknowledgement"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        ACK_REMINDED = "ack_reminded", "Acknowledgement reminder sent"
        PARTICIPANTS_CHANGED = "participants_changed", "Participants updated"
        ARCHIVED = "archived", "Archived"
        DELETED = "deleted", "Deleted"
        EXPORTED = "exported", "Exported"

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
    minute = models.ForeignKey(
        Minute, on_delete=models.CASCADE, related_name="audit_entries")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="minute_audit_entries")
    actor_name = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=24, choices=Action.choices, db_index=True)
    remarks = models.TextField(blank=True)
    # Resolved through config.client_ip, which walks X-Forwarded-For only as far as our
    # own proxies. Reading REMOTE_ADDR behind nginx records the proxy's address on
    # every row, which looks like evidence and is worse than an empty column.
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["minute", "created_at"]),
            models.Index(fields=["action", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.minute_id} {self.action} {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def actor_display(self):
        if self.actor_id and self.actor:
            return self.actor.get_full_name() or self.actor.username
        return self.actor_name or "System"
