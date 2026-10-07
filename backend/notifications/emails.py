"""Email rendering + delivery for notifications (HTML + plain-text fallback).

Delivery is fail-safe: every send is wrapped, its outcome recorded in
NotificationLog (sent/failed + error), and exceptions never propagate to the
request path. In prod each send runs in a background thread so the API response
is never blocked; NOTIFICATIONS_RUN_SYNC=True (tests / small deploys) runs inline.
"""
import logging
import threading

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import connection
from django.template.loader import render_to_string

from .models import Category

logger = logging.getLogger("notifications.email")


def _template_for(category):
    if str(category).startswith("MEMO"):
        return "emails/memo_action.html"
    if category in (Category.LEAVE_SUBMITTED, Category.LEAVE_APPROVED, Category.LEAVE_REJECTED):
        return "emails/leave_action.html"
    if category == Category.LEAVE_BALANCE_LOW:
        return "emails/balance_alert.html"
    return "emails/generic.html"


def _frontend(path=""):
    base = getattr(settings, "FRONTEND_URL", "http://localhost:5173").rstrip("/")
    if path and path.startswith("/"):
        return f"{base}{path}"
    return path or base


# The module a category belongs to, taken from its prefix. Categories are named
# MODULE_EVENT throughout, so this needs no table and cannot drift out of step
# with the enum — a new category is classified the moment it is added.
_MODULES = {
    "LEAVE": "Leave", "MEMO": "Memo", "MINUTE": "Minute", "CIRCULAR": "Circular",
    "TASK": "Task", "APPRAISAL": "Appraisal", "INVENTORY": "Inventory",
    "BALANCE": "Leave", "WEEKLY": "Digest",
}


def module_for(category):
    """The human module name behind a category, or 'System' if it has no prefix."""
    head = str(category).split("_", 1)[0].upper()
    return _MODULES.get(head, "System")


def _branding():
    """``logo_url``, ``org_name`` and the accent colour for this tenant.

    Falls back to the platform's own values when there is no tenant in
    context -- a platform notice, or the single-tenant deployment where
    `ORG_INFO` IS the organization. Never raises: an email that cannot be
    branded must still be sent.
    """
    defaults = {
        # The PLATFORM's logo and name, for a tenant that has uploaded
        # neither. Brand migration: this was NIF's logo and NIF's name, so
        # an unbranded customer's staff were emailed under the previous
        # platform owner's identity.
        "logo_url": _frontend("/buddhi-labs.png"),
        "org_name": getattr(settings, "ORG_INFO", {}).get(
            "name", getattr(settings, "PLATFORM_NAME", "Buddhi Labs")),
        "brand_color": "",
    }
    try:
        from tenancy.scoping import active_organization

        organization = active_organization(required=False)
        if organization is None:
            return defaults
        branding = getattr(organization, "branding", None)
        name = (getattr(branding, "display_name", "") or "").strip()
        logo = (getattr(branding, "logo_email", None)
                or getattr(branding, "logo_primary", None))
        return {
            "logo_url": (_absolute_media(logo) if logo
                         else defaults["logo_url"]),
            "org_name": name or organization.name or defaults["org_name"],
            "brand_color": (getattr(branding, "color_primary", "") or ""),
        }
    except Exception:                              # noqa: BLE001
        logger.debug("could not resolve tenant branding for an email",
                     exc_info=True)
        return defaults


def powered_by():
    """The platform's attribution line, for the footer of every email.

    Under the tenant's own branding rather than instead of it: the header
    says whose workspace this is, the footer says who runs the software.
    """
    return getattr(settings, "PLATFORM_POWERED_BY", "Powered by Buddhi Labs")


def subject_prefix():
    """What goes in square brackets at the front of a subject line.

    THE TENANT'S NAME, FALLING BACK TO THE PLATFORM'S. Subjects read
    "[NIF Portal] Your weekly summary" for every customer -- the previous
    platform owner's name in the inbox of somebody who has never heard of
    them, and the part of an email most likely to be read before it is
    deleted.

    Resolved per send, because these go out one recipient at a time and the
    tenant in context is the recipient's own.
    """
    name = _branding().get("org_name") or getattr(
        settings, "PLATFORM_NAME", "Buddhi Labs")
    return f"[{name}]"


def _absolute_media(field):
    """An absolute URL for a stored image, for an email client to fetch.

    NOT a signed media link. A signed URL expires (300s by default), and an
    email is read hours or weeks after it is sent -- a logo behind a signed
    link is a broken image in every archived message. Branding images are
    not confidential: they are what the customer shows the world.
    """
    try:
        url = field.url
    except Exception:                              # noqa: BLE001
        return None
    if url.startswith("http"):
        return url
    return f"{_site_url().rstrip('/')}{url}"


def _site_url():
    """The public base URL, per tenant where one is configured.

    `documents.pdf.base_site_url` is the one place that already answers this
    -- it resolves the tenant's own `site_url` before the platform setting,
    which is what makes a verification QR code on a PDF point at the right
    host. An email's logo has the same requirement, so it asks the same
    function rather than growing a second answer.
    """
    try:
        from documents.pdf import base_site_url
        from tenancy.scoping import active_organization

        return (base_site_url(active_organization(required=False))
                or getattr(settings, "SITE_URL", ""))
    except Exception:                              # noqa: BLE001
        return getattr(settings, "SITE_URL", "")


def _context(recipient_name, category, title, body, action_url, extra=None):
    ctx = {
        "recipient_name": recipient_name,
        "title": title,
        "body": body,
        "action_url": _frontend(action_url),
        "unsubscribe_url": _frontend("/notifications"),
        "category_label": dict(Category.choices).get(category, str(category)),
        # Branding -- THE TENANT'S, not the platform's (Phase S9 Part 6).
        #
        # These two lines were `/NIF.png` and `settings.ORG_INFO["name"]`,
        # which meant every email this platform sent to every customer's
        # employees arrived branded as Nepal Internet Foundation. A leave
        # approval for a hospital, signed by a school. `_branding()` resolves
        # it from the tenant the email is being sent FOR -- which is
        # available because `dispatch_email` already enters that tenant's
        # context before rendering (the Phase S6 fix for background sends).
        **_branding(),
        "powered_by": powered_by(),
        "product_name": "Office Management System",
        "module_name": module_for(category),
        # `reference` is the human document number (CIR-2026-000001 and the
        # like). `request_ref` stays the UUID where a module has no such number
        # — leave being the case that has one and not the other.
        "reference": "",
    }
    if extra:
        ctx.update(extra)
    return ctx


def _record_log(recipient, recipient_email, category, object_id, subject, status,
                error="", reference=""):
    """Best-effort NotificationLog write. Runs in the (possibly background) send
    thread, so it opens/closes its own connection and never raises."""
    try:
        from .models import NotificationLog
        NotificationLog.objects.create(
            recipient=recipient, recipient_email=recipient_email or "",
            category=str(category), object_id=str(object_id or ""),
            reference=str(reference or "")[:64],
            subject=subject[:255], status=status, error=(error or "")[:2000],
        )
    except Exception:  # noqa: BLE001 - logging must never break delivery
        logger.exception("NotificationLog write failed (category=%s to=%s)", category, recipient_email)
    finally:
        # In a spawned thread Django won't auto-close the connection; leaking one
        # per email would exhaust the pool.
        if getattr(settings, "NOTIFICATIONS_RUN_SYNC", False) is False:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass


def send_notification_email(user_email, recipient_name, category, title, body, action_url,
                            extra=None, object_id="", recipient=None):
    """Render + send one notification email and record the outcome. Template
    rendering + SMTP touch no shared state, so this is safe in a background thread."""
    subject = f"[NIF] {title}"
    try:
        ctx = _context(recipient_name, category, title, body, action_url, extra)
        reference = ctx.get("reference") or ctx.get("request_ref") or ""
        html = render_to_string(_template_for(category), ctx)
        text = render_to_string("emails/notification.txt", ctx)
        msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [user_email])
        msg.attach_alternative(html, "text/html")
        msg.send(fail_silently=False)  # we handle failures ourselves (log + swallow)
        _record_log(recipient, user_email, category, object_id, subject, "sent",
                    reference=reference)
    except Exception as exc:  # noqa: BLE001 - never break the workflow on email
        logger.warning("Notification email failed to=%s category=%s: %s", user_email, category, exc)
        _record_log(recipient, user_email, category, object_id, subject, "failed",
                    str(exc), reference=locals().get("reference", ""))


def dispatch_email(user, category, title, body, action_url, extra=None, object_id=""):
    args = (user.email, (user.get_full_name() or user.username), category, title, body, action_url)
    kwargs = {"extra": extra, "object_id": object_id, "recipient": user}
    if getattr(settings, "NOTIFICATIONS_RUN_SYNC", False):
        send_notification_email(*args, **kwargs)
        return

    # THE SEND THREAD BINDS THE RECIPIENT'S TENANT (Phase S6).
    #
    # A thread gets its own database connection, and `app.current_org` is
    # connection state -- so this thread inherits nothing from the request
    # that spawned it. The email itself would still go out, but the
    # NotificationLog row `_record_log` writes carries the recipient's
    # organization, and under row-level security an unbound connection cannot
    # write it: the policy's WITH CHECK refuses it, `_record_log` swallows the
    # failure by design, and the delivery record is quietly lost. Which is
    # the worst shape of bug available here, because NotificationLog exists
    # precisely to answer "was this ever sent?".
    organization_id = getattr(user, "organization_id", None)
    threading.Thread(target=_send_in_tenant,
                     args=(organization_id, args, kwargs), daemon=True).start()


def _send_in_tenant(organization_id, args, kwargs):
    """Enter the recipient's tenant, then send. See dispatch_email."""
    from tenancy.context import tenant_context

    if organization_id is None:
        # A platform account, or a recipient with no tenant. Nothing to bind,
        # and NotificationLog's organization is nullable for exactly this.
        send_notification_email(*args, **kwargs)
        return
    with tenant_context(organization_id):
        send_notification_email(*args, **kwargs)
