import uuid

from django.conf import settings
from django.db import models
from tenancy.scoping import AllTenantsManager, TenantManager


class IssuedDocument(models.Model):
    """
    Registry of every PDF the system issues, enabling public verification via
    the document number embedded in the PDF's QR code. Holds only
    non-sensitive metadata (who/when/validity), never the document body.
    """
    class DocType(models.TextChoices):
        MEMO = "memo", "Memo"
        LEAVE_APPLICATION = "leave_application", "Leave Application"
        LEAVE_CERTIFICATE = "leave_certificate", "Leave Certificate"

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
    document_number = models.CharField(max_length=64, db_index=True)
    doc_type = models.CharField(max_length=30, choices=DocType.choices)

    target_type = models.CharField(max_length=30)  # "memo" | "leave"
    target_id = models.UUIDField()

    subject = models.CharField(max_length=255, blank=True, default="")
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents_issued",
    )
    actors = models.JSONField(default=list, blank=True)  # names shown on verify page
    is_valid = models.BooleanField(default=True)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at"]
        # Phase S4: was unique=True on `document_number` alone -- a GLOBAL namespace.
        # Phase S3 already gives each tenant its own document prefix, so a
        # collision was impossible in practice; the composite makes it
        # impossible by construction and removes the last way one tenant's
        # numbering could refuse another tenant's insert.
        constraints = [
            models.UniqueConstraint(fields=["organization", "document_number"],
                                    name="uniq_document_number_org"),
        ]

    def __str__(self):
        return f"{self.document_number} ({self.doc_type})"
