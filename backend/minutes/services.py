"""
Minute primitives that are not workflow-stage-specific: numbering, the audit
writer, and the participant/involvement snapshot helpers.

REUSED, NOT REWRITTEN
---------------------
  * HTML sanitization  -> memos.sanitizers.sanitize_memo_html. Sanitizing authored
    HTML has nothing to do with which module authored it, and that function is the
    single hardened gate in this codebase: a 40-tag allowlist, a CSS property
    allowlist, a raster-only data-URI rule for images, and url() stripping to stop
    WeasyPrint fetching anything at render time. A second copy would be a second
    thing to keep in step, and the copy that lagged would be the vulnerable one.
  * Audit    -> audit.services.log_action for the global trail (generic FK, client
    IP resolved through our own proxies), plus this module's own MinuteAuditLog for
    the per-minute panel and export.
  * Notify   -> notifications.dispatcher.notify, with MINUTE_* categories so a
    recipient can mute minute traffic without muting memos.
"""
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from audit.services import log_action
from config.client_ip import client_ip
from config.uploads import validate_attachment as _validate_upload
# The memo sanitizer IS the sanitizer. See the module docstring.
from memos.sanitizers import sanitize_memo_html
from .models import Minute, MinuteAuditLog, MinuteNumberSequence
from config.uploads import DOCUMENT_ATTACHMENT_EXTENSIONS

logger = logging.getLogger("minutes")

MIN_REMARK_LENGTH = 10

# What a minute may carry (Phase 42 item 1): PDF, Word, Excel, PowerPoint, images.
#
# Passed EXPLICITLY into the shared validator rather than widening its defaults, so
# adding PowerPoint support to minutes does not silently also widen what memos and
# attendance corrections accept. The MIME mapping for these lives in config.uploads
# beside the rest.
MINUTE_ATTACHMENT_EXTENSIONS = DOCUMENT_ATTACHMENT_EXTENSIONS | {
    # A SUPERSET, on purpose. The shared policy is the floor, not a ceiling:
    # board packs carry slide decks and the odd animated diagram, and neither
    # was asked to be removed. Narrowing minutes to the shared eleven would be
    # a regression nobody requested.
    "pptx", "gif",
}

# Board packs run large - a scanned annexure alone can be several megabytes - so the
# minute cap is higher than the 10MB project default.
MAX_MINUTE_ATTACHMENT_SIZE = 25 * 1024 * 1024

__all__ = [
    "MAX_MINUTE_ATTACHMENT_SIZE", "MINUTE_ATTACHMENT_EXTENSIONS",
    "MIN_REMARK_LENGTH", "generate_minute_number",
    "record_audit", "require_remarks", "sanitize_minute_html",
    "validate_minute_attachment", "user_snapshot",
]


def validate_minute_attachment(uploaded):
    """
    Validate one uploaded file against the minute allowlist.

    Delegates to the project validator, which checks size, extension AND the leading
    bytes - so a .docx that is really an HTML file is refused rather than stored and
    served back later.
    """
    return _validate_upload(
        uploaded,
        max_size=MAX_MINUTE_ATTACHMENT_SIZE,
        extensions=MINUTE_ATTACHMENT_EXTENSIONS,
    )


def sanitize_minute_html(raw):
    """
    Sanitize authored minute HTML.

    A thin alias over the memo sanitizer rather than a fork: minute bodies allow
    exactly what memo bodies allow (tables, financial tables, images, checklists,
    links, formatting - Phase 31-D's "no feature downgrade"), and the four allowlists
    that gate it must move together. If minutes ever need a different policy this is
    where it would diverge, deliberately and visibly.
    """
    return sanitize_memo_html(raw)


@transaction.atomic
def generate_minute_number():
    """
    Return the next minute number in the form MIN-YYYY-000001, resetting each
    calendar year.

    Handed out by an authoritative per-year counter row locked with
    select_for_update(), so concurrent creations serialise on the lock rather than
    racing on a "max existing number" read. Six digits of padding, and because the
    counter is an integer the sequence keeps working past 999999 - it just widens.
    """
    from tenancy import numbering
    from tenancy.scoping import active_organization

    organization = active_organization()
    year = timezone.now().year
    # Phase S2: per (organization, year). Was one row for the whole platform.
    # get_or_create is safe under the unique constraint if two creators race for the
    # first number of the year; the loser retries the get.
    MinuteNumberSequence.objects.get_or_create(organization=organization, year=year)
    seq = (MinuteNumberSequence.objects.select_for_update()
           .get(organization=organization, year=year))
    seq.last_value += 1
    seq.save(update_fields=["last_value"])
    return numbering.format_number("MIN", year=year, value=seq.last_value,
                                   width=6, organization=organization)


def user_snapshot(user):
    """
    Name / designation / department for a user, frozen at the moment of recording.

    Every historical row keeps these alongside a SET_NULL foreign key, so a minute
    from three years ago still names the person who signed it even if the account has
    since been deleted.
    """
    if user is None:
        return {"name": "", "designation": "", "department": ""}
    return {
        "name": (user.get_full_name() or user.username or "")[:150],
        "designation": (getattr(user, "designation", "") or "")[:120],
        "department": (
            getattr(getattr(user, "department_ref", None), "name", None)
            or getattr(user, "department", "") or ""
        )[:120],
    }


def record_audit(minute, actor, action, remarks="", metadata=None, request=None):
    """
    Write one immutable audit row - to this module's trail AND the global one.

    Called inside the same transaction as the state change it describes, so a rolled
    back transition cannot leave an audit row claiming it happened.
    """
    snapshot = user_snapshot(actor)
    entry = MinuteAuditLog.objects.create(
        minute=minute,
        actor=actor if actor is not None and getattr(actor, "is_authenticated", False) else None,
        actor_name=snapshot["name"],
        action=action,
        remarks=remarks or "",
        ip_address=client_ip(request) if request is not None else None,
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:512] if request is not None else ""),
        metadata=metadata or {},
    )
    logger.info(
        "minute audit action=%s actor=%s target=%s",
        action, getattr(actor, "id", None), minute.minute_number,
    )
    # The global trail as well, so cross-module audit queries see minute activity.
    log_action(actor, action, instance=minute, changes=metadata or {}, request=request)
    return entry


def require_remarks(remarks, minimum=MIN_REMARK_LENGTH):
    remarks = (remarks or "").strip()
    if len(remarks) < minimum:
        raise ValidationError(
            {"remarks": f"A remark of at least {minimum} characters is required."}
        )
    return remarks


def notify_user(recipient, category, title, body, minute):
    """
    Best-effort notification. A delivery failure must never roll back a workflow
    transition, but it is logged at ERROR with the minute number so it is alertable
    rather than silent.
    """
    if recipient is None:
        return
    try:
        from notifications.dispatcher import notify
        notify(recipient, category, title, body,
               action_url=f"/minutes/{minute.id}", object_id=str(minute.id))
    except Exception:  # noqa: BLE001 - notifications must not fail the workflow
        logger.error(
            "minute notification failed minute=%s category=%s recipient=%s",
            minute.minute_number, category, getattr(recipient, "id", None),
            exc_info=True,
        )


def touch_department(minute):
    """
    Fill the department snapshot from the initiator if the caller left it blank, so
    department scoping and the department chart have something to group by.
    """
    if minute.department_id is None:
        ref = getattr(minute.created_by, "department_ref", None)
        if ref is not None:
            minute.department = ref
    if not minute.department_name:
        minute.department_name = (
            minute.department.name if minute.department_id and minute.department
            else (getattr(minute.created_by, "department", "") or "")
        )[:120]
    return minute


def minute_status_label(minute):
    return Minute.Status(minute.status).label
