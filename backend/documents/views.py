"""Public document verification (no authentication).

WHAT THIS ENDPOINT IS FOR
-------------------------
A PDF this system issues carries a QR code pointing here. Somebody holding a
printed memo or leave certificate scans it and is told whether the document is
genuine. That is the whole purpose, and it has to work for a reader with no
account -- so the endpoint is, and must remain, unauthenticated.

THREE THINGS WERE WRONG FOR MULTI-TENANCY (Phase S3)
----------------------------------------------------
1. **It resolved document numbers across every tenant.** Any host would answer
   for any tenant's document. The lookup is now narrowed to the tenant the
   request's host resolves to, so `nif.platform.com` cannot be asked about
   another company's paperwork at all.

2. **Document numbers are SEQUENTIAL, so they enumerate.** `NIFN-LV-2026-0001`,
   `-0002`, `-0003`. The old response handed out the document's SUBJECT and the
   NAMES OF ITS SIGNATORIES for any number anybody typed -- so a stranger could
   walk the sequence and harvest a staff directory and a list of who approved
   what. The public payload is now reduced to what verification actually needs:
   is this document genuine, what kind is it, and when was it issued. Names and
   subject are gone.

3. **There was no rate limit.** A plain function view gets none of DRF's
   throttling, so enumeration was free. It is now throttled per IP.

WHAT A CALLER CANNOT LEARN
--------------------------
Whether a number belongs to another tenant, or does not exist at all: both
answer 404 with an identical body. That matters because a distinguishable
response would turn this endpoint into a cross-tenant existence oracle even
with the payload reduced.

OWNERSHIP, NOT INFERENCE (Phase S4 -- closes R7')
-------------------------------------------------
Phase S3 scoped this endpoint by comparing the DOCUMENT NUMBER PREFIX against
the tenant's own prefix. That was sound -- Phase S2 made every tenant's prefix
distinct -- but it was inference: it asked "does this number look like one of
ours?" rather than "do we own this row?".

Phase S4 gave `IssuedDocument` an `organization` foreign key, so the lookup is
now a filter on actual ownership. Three things improve:

  * a tenant on the LEGACY unprefixed formats (NIF is one) is scoped correctly,
    where prefix comparison had nothing to compare;
  * a document whose number was edited, imported or back-filled is still owned
    by whoever owns the row, not by whoever owns the string; and
  * there is one source of truth instead of two that can drift.
"""
import logging

from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.cache import never_cache
from rest_framework.decorators import (api_view, authentication_classes,
                                       permission_classes, throttle_classes)
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle

from .models import IssuedDocument

logger = logging.getLogger(__name__)


class VerificationRateThrottle(AnonRateThrottle):
    """Its own bucket, because enumeration is the threat here.

    Document numbers are sequential. Without a limit, walking a tenant's whole
    issued-document history is a shell loop. This is deliberately tighter than
    the global anonymous rate and separate from it, so a verification flood
    cannot also exhaust the budget for the login page.
    """
    scope = "verify"


def _organization_for(request):
    """The tenant this request may ask about, or None.

    Resolved from the host by TenantResolutionMiddleware. While
    TENANCY_ENABLED is False an unrecognised host falls back to the single
    organization, which is exactly today's behaviour for NIF.
    """
    organization = getattr(request, "organization", None)
    return organization if organization is not None and bool(organization) else None


def _lookup(document_number, organization):
    """The document, IF this tenant owns it. Otherwise None.

    A filter on ``organization``, not a prefix comparison (Phase S4). With no
    tenant resolved at all, nothing is looked up: the caller gets the
    not-found answer rather than an unscoped query.
    """
    if organization is None:
        return None
    return (IssuedDocument.objects
            .filter(organization=organization,
                    document_number=document_number)
            .first())


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([VerificationRateThrottle])
@never_cache
def verify_document(request, document_number):
    organization = _organization_for(request)

    doc = _lookup(document_number, organization)
    if doc is None and organization is not None:
        logger.info(
            "verification found no document %s owned by %s",
            str(document_number)[:64], organization.slug)

    # DELIBERATELY MINIMAL. See the module docstring: `subject` and `actors`
    # used to be returned here and were a staff-directory leak over a
    # sequential, unauthenticated, unthrottled endpoint.
    data = {
        "document_number": document_number,
        "valid": bool(doc and doc.is_valid),
        "doc_type": doc.get_doc_type_display() if doc else None,
        "issued_at": doc.issued_at.isoformat() if doc else None,
    }
    status = 200 if doc else 404

    wants_json = (request.GET.get("format") == "json"
                  or "application/json" in request.headers.get("Accept", ""))
    if wants_json:
        return JsonResponse(data, status=status)
    html = render_to_string("verify/verify.html", {"d": data})
    return HttpResponse(html, status=status)
