"""
Autosave storage for Memo, Minute and Circular (Phase 111).

WHAT THIS IS NOT
----------------
This is not a workflow stage. The three modules keep their own status ladders
untouched — Memo's eight states, Minute's four, Circular's nine — and nothing in
this app writes to them. A draft snapshot is *working state*: the bytes a user
has typed but not yet committed through the module's own save path.

That separation is the whole design. "Auto saved" answers "how recently did this
reach disk", which is a different question from "where is this in its approval
journey", and conflating the two would put an autosave concept into every
dashboard tile, filter, permission check and PDF in the system.

WHY ONE GENERIC TABLE RATHER THAN THREE
---------------------------------------
The payload is opaque here — the server never looks inside it — so the retention
rule, the cleanup job, the ownership check and the version trimming are
identical for all three modules. Three near-identical tables would be three
places to fix the next bug in any of them.

WHY THE PAYLOAD IS NOT VALIDATED
--------------------------------
Memo.subject, Circular.subject/body and Minute.meeting_time/minute_type are all
`blank=False`. A user who opens Create Memo and starts typing the body has no
subject yet, so an autosave routed through the real create serializer would 400
five seconds in and keep 400ing. Validation belongs at submit, where the user
is asserting the document is finished. Here it would only guarantee that the
people who most need autosave — the ones part-way through — are the ones who
do not get it.
"""
import uuid

from django.conf import settings
from django.db import models
from tenancy.scoping import AllTenantsManager, TenantManager


class DocumentDraft(models.Model):
    """
    The rolling snapshot: exactly one row per (kind, document, user), overwritten
    on every autosave.

    Scoped to the user and not just the document because two people editing the
    same draft must not silently overwrite each other's recovery copy. The
    module's own concurrency rules still govern the real record.
    """

    class Kind(models.TextChoices):
        MEMO = "memo", "Memo"
        MINUTE = "minute", "Minute"
        CIRCULAR = "circular", "Circular"
        # Phase TASK-AUTOSAVE-AND-SUBTASKS. The task form autosaves here too;
        # unlike the three above, a task's key may belong to somebody else's
        # record, so drafts.views guards the kind with the task's own edit rule.
        TASK = "task", "Task"

    #: Sentinel document key for a document that has never been saved, so a
    #: half-typed "Create Memo" that has no server-side row yet still has a
    #: place to live. Only ever one per user per kind, which is also the rule
    #: the UI enforces by offering recovery when they return to the form.
    NEW = "new"

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

    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)
    # A UUID string for an existing document, or NEW. CharField rather than a
    # real FK: there is nothing to point at while the document is still unsaved,
    # and a nullable FK plus a sentinel column would be two states to keep in
    # step instead of one.
    document_key = models.CharField(max_length=64, db_index=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="document_drafts", db_index=True)

    payload = models.JSONField(default=dict, blank=True)

    # Monotonic per draft. The client sends the version it last saw; a mismatch
    # means another device wrote in between, which the recovery dialog surfaces
    # rather than resolving silently.
    version = models.PositiveIntegerField(default=0)

    # How many times autosave has written. Counted here rather than as audit
    # rows: a five-second cadence would put hundreds of entries on one document
    # and drown the workflow trail it shares a table with.
    autosave_count = models.PositiveIntegerField(default=0)

    # Free text from the client ("Chrome on macOS"), shown in recovery so a user
    # can tell which machine the newer copy came from. Never trusted, never
    # parsed, truncated on write.
    device_label = models.CharField(max_length=120, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    saved_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "document_key", "owner"],
                name="uniq_draft_per_document_per_user"),
        ]
        indexes = [
            # The cleanup job's only query.
            models.Index(fields=["saved_at"], name="draft_saved_at_idx"),
            # "What unfinished work do I have?" across all three modules.
            models.Index(fields=["owner", "-saved_at"], name="draft_owner_recent_idx"),
        ]
        ordering = ["-saved_at"]

    def __str__(self):
        return f"{self.get_kind_display()} draft {self.document_key} ({self.owner_id})"

    @property
    def is_unsaved_document(self):
        """True while this draft has no real document behind it yet."""
        return self.document_key == self.NEW


class DocumentDraftVersion(models.Model):
    """
    A retained milestone, so "restore an earlier version" shows something
    meaningfully different.

    NOT one row per autosave. At a five-second cadence ten versions would span
    the last four minutes of typing and every one of them would look the same.
    A version is written only on an event that marks a coherent stopping point —
    the user saved, switched away, or crossed the interval — which is what makes
    the list worth reading.
    """

    class Reason(models.TextChoices):
        MANUAL_SAVE = "manual_save", "Manual save"
        TAB_HIDDEN = "tab_hidden", "Switched away"
        INTERVAL = "interval", "Periodic"
        BEFORE_RESTORE = "before_restore", "Superseded by a restore"

    #: Versions retained per draft. The oldest is trimmed past this.
    KEEP = 10

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

    draft = models.ForeignKey(
        DocumentDraft, on_delete=models.CASCADE, related_name="versions")

    version = models.PositiveIntegerField()
    payload = models.JSONField(default=dict, blank=True)
    reason = models.CharField(
        max_length=20, choices=Reason.choices, default=Reason.INTERVAL)

    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+")
    saved_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-version"]
        indexes = [models.Index(fields=["draft", "-version"], name="draftver_recent_idx")]

    def __str__(self):
        return f"v{self.version} of {self.draft_id}"
