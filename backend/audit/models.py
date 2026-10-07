import uuid
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from tenancy.scoping import AllTenantsManager, TenantManager

class AuditLog(models.Model):
    """
    Immutable record of an action taken against any model in the system.
    Written only via audit.services.log_action() - never updated or deleted.
    """
    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        APPROVE = "approve", "Approve"
        REJECT = "reject", "Reject"
        SUBMIT = "submit", "Submit"
        LOGIN = "login", "Login"
        OTHER = "other", "Other"

        # THE WORKFLOW VERBS (Phase OMS-FINAL-RELEASE-BLOCKERS).
        #
        # These four decisions used to land here as UPDATE. The module's own
        # history knew exactly what had happened — MemoWorkflowStep.
        # COMPLETION_ACTION already maps each role to its verb, and the verb was
        # even written into this row's metadata — but the indexed `action`
        # column, which is what a compliance query filters on, said "update" for
        # a review, a recommendation and a statement of support alike.
        #
        # An audit trail that cannot distinguish a recommendation from an edit
        # is not an audit trail. Reading them apart required joining back to the
        # workflow steps, which is exactly the reconstruction an audit log
        # exists to make unnecessary.
        REVIEWED = "reviewed", "Reviewed"
        RECOMMENDED = "recommended", "Recommended"
        SUPPORTED = "supported", "Supported"
        ARCHIVED = "archived", "Archived"

        # The moment an asset changed hands (Phase ASSET-TRANSFER-GOVERNANCE), for
        # the same reason as the four above: "who has held this asset, and when did
        # it move" is the question an asset audit is FOR, and it used to land here
        # as UPDATE, indistinguishable from someone correcting a serial number.
        OWNERSHIP_CHANGED = "ownership_changed", "Ownership Changed"

        # A decision taken BY the Board of Directors (Phase BOD-ROLE-EXECUTIVE-
        # GOVERNANCE) - today, a Department Head's leave. Its own verbs rather
        # than APPROVE/REJECT because "who decided at board level" is a question
        # a governance audit asks directly, and it must not require joining back
        # to the actor's role at the time.
        BOD_APPROVED = "bod_approved", "BOD Approved"
        BOD_REJECTED = "bod_rejected", "BOD Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). DELIBERATELY NULLABLE --
    # a failed login for an address belonging to nobody has no tenant, and
    # inventing one would hide the probe; platform events have none either.
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

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs", db_index=True)
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    object_id = models.CharField(max_length=64, null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")
    object_repr = models.CharField(max_length=255, blank=True, default="")

    changes = models.JSONField(blank=True, default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def save(self, *args, **kwargs):
        # `all_tenants`, NOT `objects` (Phase S6).
        #
        # This is an IDENTITY check -- "is there already a row with this
        # primary key?" -- not a tenant query, and the answer must not depend
        # on which tenant happens to be in context. Through the scoped
        # manager it did, three ways:
        #
        #   * bound to tenant A, an existing row owned by B is invisible, so
        #     the guard passes and the immutable row is overwritten;
        #   * in PLATFORM scope (an unowned row -- a failed login for an
        #     address belonging to nobody, or a platform operator's own
        #     action), the scoped manager has no tenant and, with
        #     TENANCY_ENABLED on, RAISES. Every platform sign-in answered 500.
        #   * row-level security applies the same narrowing a second time.
        #
        # A primary key is globally unique here (UUID), so an unscoped read is
        # exactly the right question.
        if self.pk and AuditLog.all_tenants.filter(pk=self.pk).exists():
            raise ValueError("AuditLog entries are immutable and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog entries are immutable and cannot be deleted.")

    def __str__(self):
        return f"{self.action} on {self.object_repr or self.content_type} by {self.actor} at {self.created_at}"
