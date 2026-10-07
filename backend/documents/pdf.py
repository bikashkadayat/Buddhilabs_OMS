"""PDF rendering + QR helpers shared by the memo/leave document endpoints."""
import base64
import io
import logging

from django.conf import settings
from django.template.loader import render_to_string


def qr_data_uri(url):
    """Return a base64 PNG data-URI QR code for `url` (embeddable in HTML)."""
    import qrcode

    img = qrcode.make(url, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# Hosts that mean "this was built on somebody's laptop". A verification link
# pointing at one of these is worthless on a printed document and cannot be
# corrected afterwards, because the PDF has already been filed.
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "host.docker.internal")

# Tenant token -> data URI. A DICT, not a single value: see logo_data_uri().
logger = logging.getLogger(__name__)

_LOGO_CACHE = {}


def base_site_url(organization=None):
    """The public base URL for this TENANT's documents (Phase S3).

    ``Organization.site_url`` first, then the platform-wide ``SITE_URL``. The
    per-tenant value matters because the QR code on a printed document has to
    resolve to the tenant's own host: a verification link pointing at
    nif.platform.com cannot answer for another company's paperwork, and after
    Phase S3 it will correctly refuse to.

    Falls back to the global setting so NIF, which has no per-tenant value set,
    keeps emitting exactly the URL it does today.
    """
    if organization is None:
        from tenancy.scoping import active_organization

        organization = active_organization(required=False)

    tenant_url = (getattr(organization, "site_url", "") or "").strip()
    return tenant_url or (getattr(settings, "SITE_URL", "") or "").strip()


def site_url_is_publishable(organization=None):
    """
    True when SITE_URL names somewhere a reader could actually reach.

    Phase 49.5: the previous default was `http://localhost:8001`, applied silently
    whenever SITE_URL was unset — including with DEBUG off. Every archived memo and
    minute exported from such a deployment carries a dead QR code permanently.
    Four other settings in config/settings.py raise ImproperlyConfigured when they
    are missing; this one did not.
    """
    base = base_site_url(organization)
    if not base:
        return False
    lowered = base.lower()
    return not any(f"//{host}" in lowered or f"//{host}:" in lowered
                   for host in _LOCAL_HOSTS)


def verify_url(document_number, organization=None):
    """
    The public verification URL, or None when no publishable base URL exists.

    Returning None rather than a localhost URL is the point: callers render a
    visible warning instead of a QR code nobody can use, so a misconfiguration
    shows up on the face of the first document somebody looks at rather than
    silently on all of them.
    """
    if not site_url_is_publishable(organization):
        return None
    return (f"{base_site_url(organization).rstrip('/')}"
            f"/api/v1/verify/{document_number}/")


def logo_data_uri(organization=None):
    """Base64 data-URI of the letterhead logo, so it embeds rather than linking.

    PER TENANT, AND THE CACHE IS KEYED BY TENANT (Phase S3).

    This was a single module-level ``_LOGO_CACHE`` global holding whichever
    logo was read first. In a multi-tenant process that is a cross-tenant
    disclosure with no query in it at all: the first tenant to export a PDF
    after a restart would have ITS logo baked into every other tenant's memos,
    minutes, gate passes and leave certificates -- documents that then get
    printed, signed and filed.

    Resolution order, falling back so NIF is unchanged:
      1. the organization's own branding logo (OrganizationBranding.logo_letterhead)
      2. the organization's logo
      3. the bundled default on disk
    """
    from tenancy.keys import token_for

    if organization is None:
        from tenancy.scoping import active_organization

        organization = active_organization(required=False)

    token = token_for(getattr(organization, "pk", None)) if organization else "default"
    if token in _LOGO_CACHE:
        return _LOGO_CACHE[token]

    uri = _read_tenant_logo(organization) or _read_default_logo()
    _LOGO_CACHE[token] = uri
    return uri


def forget_logo(organization=None):
    """Drop the cached logo for one tenant, or for all of them.

    PHASE S9. Nothing evicted this cache. A customer who uploaded a new
    letterhead logo kept getting the old one on every PDF until the process
    restarted -- and with several workers, unpredictably: some documents
    carried the new mark and some the old, which is worse than consistently
    stale because it looks like a printing fault rather than a bug.

    Called from both branding writers, the customer's and the operator's.
    """
    if organization is None:
        _LOGO_CACHE.clear()
        return
    from tenancy.keys import token_for

    _LOGO_CACHE.pop(token_for(getattr(organization, "pk", organization)), None)


def _branding_row(organization):
    """The tenant's branding, READ FROM THE DATABASE.

    `organization.branding` is a reverse one-to-one that Django caches on the
    instance, and provisioning creates the row through that instance -- so an
    organization object that has been in hand since before a branding change
    answers with the branding as it was then. A document rendered from such
    an object carried the old name. One indexed lookup, on a path that is
    already rendering a PDF.
    """
    if organization is None:
        return None
    try:
        from tenancy.models import OrganizationBranding

        return (OrganizationBranding.objects
                .filter(organization=organization)
                .first())
    except Exception:                              # noqa: BLE001
        # A letterhead must never be the reason a document fails to render.
        logger.warning("branding for %r could not be read",
                       getattr(organization, "slug", organization))
        return getattr(organization, "branding", None)


def _read_tenant_logo(organization):
    """The tenant's own uploaded letterhead logo, as a data URI, or None."""
    if organization is None:
        return None
    branding = _branding_row(organization)
    candidates = [getattr(branding, "logo_letterhead", None) if branding else None,
                  getattr(branding, "logo_primary", None) if branding else None,
                  getattr(organization, "logo", None)]
    for field in candidates:
        if not field:
            continue
        try:
            with field.open("rb") as handle:
                data = handle.read()
        except Exception:  # noqa: BLE001 - a missing file must not break a PDF
            logger.warning("tenant logo %r could not be read", getattr(
                field, "name", "?"))
            continue
        suffix = (getattr(field, "name", "") or "").rsplit(".", 1)[-1].lower()
        mime = "image/png" if suffix not in ("jpg", "jpeg") else "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    return None


def _read_default_logo():
    """The bundled fallback logo shipped with the application."""
    from pathlib import Path

    for path in (Path(settings.BASE_DIR) / "documents" / "assets" / "nif-logo.png",
                 Path(settings.BASE_DIR) / "static" / "branding" / "nif-logo.png"):
        if path.exists():
            return ("data:image/png;base64,"
                    + base64.b64encode(path.read_bytes()).decode("ascii"))
    return None


def common_context(document_number, organization=None):
    """Letterhead + QR verification context shared by every PDF template.

    ``organization`` defaults to the tenant in context, so the four existing
    call sites are unchanged while every document now carries its OWN tenant's
    logo and verification host (Phase S3).
    """
    from django.utils import timezone

    from config.nepali_dates import to_bs

    if organization is None:
        from tenancy.scoping import active_organization

        organization = active_organization(required=False)

    now = timezone.now()
    url = verify_url(document_number, organization)
    if url is None:
        import logging
        logging.getLogger("documents").error(
            "SITE_URL is unset or points at a local host (%r), so %s was rendered "
            "WITHOUT a verification QR code. Set SITE_URL to the public base URL.",
            base_site_url(organization) or None, document_number)
    return {
        "org": org_letterhead(organization),
        # The platform's attribution, rendered in the page footer under the
        # tenant's own letterhead. One value, read by every document type.
        "powered_by": getattr(settings, "PLATFORM_POWERED_BY",
                              "Powered by Buddhi Labs"),
        "organization": organization,
        "logo": logo_data_uri(organization),
        "document_number": document_number,
        # Both None when unpublishable. Templates render the warning below instead
        # of a broken link and an unusable QR.
        "verify_url": url,
        "verify_qr": qr_data_uri(url) if url else None,
        # No "Verification link unavailable:" prefix - the template already prints
        # that as a bold heading, and the first render said it twice in one line.
        "verify_warning": None if url else (
            "This deployment has no public site address configured (SITE_URL). "
            "Confirm this document against the system record instead."),
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "issue_date": now.strftime("%Y-%m-%d"),
        "issue_date_bs": to_bs(now.date()),
    }


def org_letterhead(organization):
    """The name, address and contact details for a letterhead.

    PHASE S9 PART 7: THE TENANT'S OWN DETAILS, not the platform's.

    The logo has been tenant-aware since Phase S3 -- `logo_data_uri` prefers
    `OrganizationBranding.logo_letterhead` -- but the TEXT beside it came
    straight from `settings.ORG_INFO`, which is one dictionary for the whole
    deployment. So every customer's memos, minutes, circulars and reports
    carried their own logo above Nepal Internet Foundation's name, address
    and telephone number: the single most visible white-label failure
    available, printed on documents customers send to third parties.

    `ORG_INFO` remains the fallback, and for the single-tenant deployment it
    is still the answer -- NIF's own organization row has the same details.
    """
    fallback = dict(getattr(settings, "ORG_INFO", {}) or {})
    if organization is None:
        return fallback

    branding = _branding_row(organization)
    display_name = (getattr(branding, "display_name", "") or "").strip()
    head = {
        **fallback,
        "name": display_name or organization.name or fallback.get("name", ""),
        "address": organization.address or fallback.get("address", ""),
        "tel": organization.phone or fallback.get("tel", ""),
        "email": organization.email or fallback.get("email", ""),
        "website": organization.site_url or fallback.get("website", ""),
    }
    head["footer_text"] = _footer_line(branding, head)
    return head


def _footer_line(branding, head):
    """The running foot of every page.

    `report_footer_text` has been offered by the console since Phase S1 and
    was stored and never printed -- the branding editor describes it as
    "printed at the foot of PDFs", which was simply untrue. It REPLACES the
    assembled contact line rather than joining it, because a customer who
    writes their own footer is usually saying something the contact line
    does not: a registration number, or "Confidential -- internal use
    only".

    SANITISED FOR A CSS STRING, not for HTML. The value lands inside
    ``content: "..."`` in a stylesheet, where Django's HTML autoescaping is
    worse than useless -- it would turn a quotation mark into `&quot;`,
    which CSS does not decode, and a raw quote would end the string and
    break the whole @page rule. So the two characters that can do that are
    removed here, at the one place the value becomes CSS.
    """
    custom = (getattr(branding, "report_footer_text", "") or "").strip()
    if custom:
        return custom.replace('"', "").replace("\\", "")
    parts = [head.get("name"), head.get("address")]
    if head.get("tel"):
        parts.append(f"Phone: {head['tel']}")
    if head.get("email"):
        parts.append(head["email"])
    return " \u00b7 ".join(p for p in parts if p)


def render_pdf(template_name, context):
    """Render a template to PDF bytes via weasyprint."""
    import weasyprint

    html = render_to_string(template_name, context)
    # base_url lets weasyprint resolve any relative static asset references.
    return weasyprint.HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
