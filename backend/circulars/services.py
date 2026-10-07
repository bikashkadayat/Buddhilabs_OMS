"""
Circular numbering, audit writing, and content import.

Everything here is stage-independent - the workflow engine lives in workflow.py and
the audience machinery in broadcast.py. This module holds the three things all of
them need.
"""
import logging

from django.db import transaction
from django.utils import timezone

from audit.services import log_action

from .models import Circular, CircularAuditLog, CircularNumberSequence
from config.uploads import DOCUMENT_ATTACHMENT_EXTENSIONS

# Circulars carry the widest mix of the three document modules — a scanned
# notice, a signed PDF, a roster spreadsheet — and were previously validated
# against the project DEFAULT set, which quietly excluded csv, doc, xls and txt
# without anyone declaring that (Phase CRITICAL-ATTACHMENT-SYSTEM-FIX).
CIRCULAR_ATTACHMENT_EXTENSIONS = set(DOCUMENT_ATTACHMENT_EXTENSIONS)

logger = logging.getLogger("circulars")

# Minimum length for any remark that carries governance weight - a review comment,
# a rejection reason, a decline. Long enough that "ok" does not pass as a reason.
MIN_REMARK_LENGTH = 10


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------
@transaction.atomic
def generate_circular_number():
    """
    The next circular number, as CIR-YYYY-000001, resetting each calendar year.

    Handed out by an authoritative per-year counter row locked with
    select_for_update(), so concurrent creations serialise on the lock rather than
    racing on a read of the highest existing number. Six digits of padding to match
    the brief; because the counter is an integer the sequence keeps working past
    999999 and simply widens.
    """
    from tenancy import numbering
    from tenancy.scoping import active_organization

    organization = active_organization()
    year = timezone.now().year
    # Phase S2: per (organization, year). Was one row for the whole platform.
    # Safe under the unique constraint if two creators race for the first number of
    # the year: the loser's get_or_create returns the winner's row.
    CircularNumberSequence.objects.get_or_create(organization=organization, year=year)
    sequence = (CircularNumberSequence.objects.select_for_update()
                .get(organization=organization, year=year))
    sequence.last_value += 1
    sequence.save(update_fields=["last_value"])
    return numbering.format_number("CIR", year=year, value=sequence.last_value,
                                   width=6, organization=organization)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def _next_sequence(circular):
    last = circular.audit_entries.order_by("-sequence").values_list(
        "sequence", flat=True).first()
    return (last or 0) + 1


def record_audit(circular, actor, action, remarks="", metadata=None, request=None):
    """
    Write BOTH logs: the circular's own history and the shared audit trail.

    Two rows, deliberately. CircularAuditLog can say "broadcast to 412 people";
    audit.AuditLog cannot - it has eight generic actions - but it carries the client
    IP and user agent, which is forensic data the module's own log has no business
    duplicating. Callers thread `request` through so those columns mean something.
    """
    entry = CircularAuditLog.objects.create(
        circular=circular, sequence=_next_sequence(circular), action=action,
        actor=actor, actor_name=describe(actor), remarks=remarks or "",
        metadata=metadata or {})
    logger.info("circular audit action=%s actor=%s target=%s",
                action, getattr(actor, "id", None), circular.circular_number)
    log_action(actor, _shared_action(action), instance=circular,
               changes={"transition": action, "remarks": remarks or "",
                        **(metadata or {})},
               request=request)
    return entry


def _shared_action(action):
    """
    Map a circular action onto the shared audit vocabulary.

    The shared table's enum is small on purpose - it is used by every app - so the
    precise action stays in the `transition` metadata key, which is what the audit
    endpoint already reads for memos.
    """
    from audit.models import AuditLog
    Action = CircularAuditLog.Action
    if action == Action.CREATED:
        return AuditLog.Action.CREATE
    if action in (Action.ISSUED, Action.REVIEWED, Action.ACKNOWLEDGED):
        return AuditLog.Action.APPROVE
    if action in (Action.REJECTED, Action.DECLINED, Action.CANCELLED):
        return AuditLog.Action.REJECT
    if action == Action.SENT_FOR_REVIEW:
        return AuditLog.Action.SUBMIT
    if action in (Action.EDITED, Action.ATTACHED, Action.ATTACHMENT_REMOVED,
                  Action.AUDIENCE_SET, Action.IMPORTED):
        return AuditLog.Action.UPDATE
    return AuditLog.Action.OTHER


def describe(user):
    if user is None:
        return "System"
    return user.get_full_name() or user.username


def _snapshot_fields(user):
    return {
        "designation": getattr(user, "designation", "") or "",
        "department_label": (getattr(user, "department_name", None)
                             or getattr(user, "department", "") or ""),
    }


def snapshot(user):
    """
    Recipient-shaped snapshot: name, designation and department as they are NOW.

    Two helpers rather than one with a mapping at each call site, because the two
    models name the person differently on purpose - a recipient has a `user_name`
    and a workflow step has an `assignee_name` - and a single dict handed to the
    wrong one fails loudly, which is how this was caught.
    """
    return {"user_name": describe(user), **_snapshot_fields(user)}


def step_snapshot(user):
    """Workflow-step-shaped snapshot."""
    return {"assignee_name": describe(user), **_snapshot_fields(user)}


def touch_department(circular):
    """Fill the department snapshot from the author when the caller left it blank."""
    if circular.department_id is None:
        reference = getattr(circular.created_by, "department_ref", None)
        if reference is not None:
            circular.department = reference
    if not circular.department_name:
        circular.department_name = (
            circular.department.name if circular.department_id and circular.department
            else (getattr(circular.created_by, "department", "") or ""))[:150]
    return circular


# ---------------------------------------------------------------------------
# Content import (the brief's "Import From Memo" / "Import From Minute")
# ---------------------------------------------------------------------------
def import_source(circular, actor, *, memo=None, minute=None, request=None):
    """
    Pull a memo's or a minute's content into a draft circular, and record where it
    came from.

    EDITABLE AFTER IMPORT, per the brief - so this fills the fields and stops. It
    is not a live link: a circular announcing a decision must not silently change
    when somebody edits the source, because the version people were told about is
    the version that was broadcast. The provenance FK keeps the trail without
    coupling the text.

    Refuses on anything but a draft, for the same reason: importing over issued
    content would rewrite an official document.
    """
    from rest_framework.exceptions import ValidationError

    if not circular.is_editable:
        raise ValidationError({"import": (
            f"This circular is {circular.get_status_display().lower()} and its "
            "content can no longer be replaced.")})
    if (memo is None) == (minute is None):
        raise ValidationError({"import": (
            "Name exactly one source: a memo or a minute.")})

    from .sanitizers import sanitize_circular_html

    if memo is not None:
        # A memo's substance is its content blocks, in their document order - the
        # memo has no single body column any more (E-memo-manual p.3). Each block's
        # title is carried across as a heading so the circular reads the way the
        # memo did rather than as one run-on passage.
        parts = []
        for section in memo.sections.all():
            if not section.body:
                continue
            if section.title:
                parts.append(f"<h3>{section.title}</h3>")
            parts.append(section.body)
        circular.memo_reference = memo
        circular.content = sanitize_circular_html("".join(parts))
        if not circular.subject:
            circular.subject = memo.subject
        source_label, source_number = "memo", memo.memo_number
    else:
        # A minute's substance is its agenda, discussion and decisions - one
        # authored body since the module was rebuilt to its own manual.
        circular.minute_reference = minute
        circular.content = sanitize_circular_html(minute.agenda_body or "")
        if not circular.subject:
            circular.subject = minute.display_title
        source_label, source_number = "minute", minute.minute_number

    circular.save(update_fields=["memo_reference", "minute_reference", "content",
                                 "subject", "updated_at"])
    record_audit(circular, actor, CircularAuditLog.Action.IMPORTED,
                 remarks=f"Content imported from {source_label} {source_number}.",
                 metadata={"source": source_label, "source_number": source_number},
                 request=request)
    return circular
