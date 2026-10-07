from django.db import transaction
from django.utils import timezone

from .models import IssuedDocument

_PREFIX = {
    IssuedDocument.DocType.LEAVE_APPLICATION: "LV",
    IssuedDocument.DocType.LEAVE_CERTIFICATE: "CERT",
}


@transaction.atomic
def _next_number(doc_type):
    """The next leave-document number for this tenant.

    Phase S2 routes the prefix through tenancy.numbering, so NIF keeps
    NIFN-LV-2026-0001 and another tenant gets its own prefix. IssuedDocument
    itself is a Phase B model and has no organization column yet, so the scan
    is still by prefix -- which is sound precisely because the prefix now
    differs per tenant.
    """
    from tenancy import numbering
    from tenancy.scoping import active_organization

    organization = active_organization()
    year = timezone.now().year
    prefix = numbering.format_number(
        _PREFIX[doc_type], year=year, value=0,
        organization=organization)[:-4]
    last = (
        IssuedDocument.objects.select_for_update()
        .filter(document_number__startswith=prefix)
        .order_by("-document_number").first()
    )
    seq = int(last.document_number.rsplit("-", 1)[-1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def issue_document(doc_type, target_type, target, subject, issued_by, actors):
    """
    Register (idempotently) an issued PDF and return its IssuedDocument. Memos
    reuse their memo_number; leave docs get a fresh sequential number.
    """
    existing = IssuedDocument.objects.filter(
        target_type=target_type, target_id=target.id, doc_type=doc_type,
    ).first()
    if existing:
        return existing

    if doc_type == IssuedDocument.DocType.MEMO:
        number = target.memo_number
    else:
        number = _next_number(doc_type)

    return IssuedDocument.objects.create(
        document_number=number, doc_type=doc_type,
        target_type=target_type, target_id=target.id,
        subject=subject or "", issued_by=issued_by if getattr(issued_by, "is_authenticated", False) else None,
        actors=actors or [],
    )
