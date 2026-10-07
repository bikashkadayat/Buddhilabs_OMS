"""
The broadcast engine: audience, recipients, read tracking and acknowledgement.

THIS IS THE PART THAT IS NOT A MEMO
-----------------------------------
A memo's distribution is its approval chain - a handful of named people, each of
whom blocks the document. A circular's distribution is a SET that may be the whole
organisation, nobody in it blocks anything, and the interesting questions are
statistical: how many received it, how many opened it, how many confirmed.

Three consequences shape the code below:

  1. Recipients are materialised ONCE, at broadcast, and stored. They are not
     recomputed per request from the audience rule. If they were, somebody who
     joined the department last week would silently appear in the distribution list
     of a circular issued last year - and the count printed on the archived PDF
     would change every time it was reprinted. A circular went to the people who
     were there.

  2. Every count is a COUNT query over those rows, never a Python loop over them.
     A thousand-recipient circular must cost the dashboard the same as a ten-
     recipient one.

  3. Re-broadcasting is additive and idempotent. Extending a circular to a
     department that was missed adds the people who are not already on it and
     reports how many were skipped, rather than either duplicating them or
     silently doing nothing.
"""
import datetime
import logging

from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from users.models import User

from .models import (
    Circular, CircularAcknowledgement, CircularAuditLog, CircularBroadcast,
    CircularReadLog, CircularRecipient,
)
from .services import MIN_REMARK_LENGTH, describe, record_audit, snapshot
from tenancy.stamping import stamp_all

logger = logging.getLogger("circulars")

Audience = CircularBroadcast.Audience
AckState = CircularAcknowledgement.State
Action = CircularAuditLog.Action
Status = Circular.Status

# How many levels of department nesting `include_child_departments` reaches.
# Bounded and resolved in the ORM rather than walked in Python, for the reason the
# memo module learned the hard way: a Python walk issues queries while building the
# rule, which puts them on every request that consults it.
DEPARTMENT_DEPTH = 4


def _is_admin(user):
    return getattr(user, "role", None) == User.Roles.ADMIN


def _is_hr(user):
    return getattr(user, "role", None) == User.Roles.APPROVER


# ---------------------------------------------------------------------------
# Audience resolution
# ---------------------------------------------------------------------------
def _department_subtree_q(department_ids, include_children):
    """
    Q matching users in the named departments, and optionally beneath them.

    Walks DOWN the `children` relation from the named departments so the traversal
    happens inside one statement. `leaves.Department` is self-nesting, so a "unit"
    and a "sub-unit" are department rows with parents - which is why the brief's
    three audience levels need one target type here and not three.
    """
    matched = Q(department_ref_id__in=department_ids)
    if not include_children:
        return matched
    path = "department_ref__parent"
    for _ in range(DEPARTMENT_DEPTH):
        matched |= Q(**{f"{path}_id__in": department_ids})
        path += "__parent"
    return matched


def resolve_audience(*, audience, department_ids=None, group_ids=None,
                     employee_ids=None, include_children=True, exclude_user=None,
                     category=None):
    """
    The set of users an audience specification names, as a queryset.

    Only ACTIVE users: broadcasting to a disabled account creates an unread row
    nobody will ever clear, which quietly makes every "unread" figure wrong for
    ever afterwards.
    """
    people = User.objects.filter(is_active=True)

    if audience == Audience.ORGANISATION:
        pass
    elif audience == Audience.DEPARTMENTS:
        if not department_ids:
            raise ValidationError({"department_ids": "Name at least one department."})
        people = people.filter(
            _department_subtree_q(department_ids, include_children))
    elif audience == Audience.GROUPS:
        if not group_ids:
            raise ValidationError({"group_ids": "Name at least one employee group."})
        people = people.filter(groups__id__in=group_ids)
    elif audience == Audience.EMPLOYEES:
        if not employee_ids:
            raise ValidationError({"employee_ids": "Name at least one employee."})
        people = people.filter(pk__in=employee_ids)
    elif audience == Audience.BOARD:
        people = people.filter(role=User.Roles.BOD)
    else:
        raise ValidationError({"audience": (
            "Choose 'organisation', 'departments', 'groups', 'employees' or 'board'.")})

    # An Executive Circular reaches the Board whatever else it is addressed to
    # (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE): a circular about the organisation's
    # direction that one Board member never received is a governance gap, not an
    # audience choice.
    if category == Circular.Category.EXECUTIVE:
        people = people | User.objects.filter(is_active=True, role=User.Roles.BOD)

    if exclude_user is not None:
        # The issuer does not need telling about their own circular, and counting
        # them makes every read percentage start at 1/n rather than 0/n.
        people = people.exclude(pk=exclude_user.pk)
    return people.distinct()


def describe_audience(*, audience, departments=None, groups=None, employees=None,
                      include_children=True):
    """A human label, snapshotted so it still reads correctly after a rename."""
    if audience == Audience.ORGANISATION:
        return "Entire organisation"
    if audience == Audience.BOARD:
        return "Board of Directors"
    if audience == Audience.DEPARTMENTS:
        names = ", ".join(sorted(d.name for d in departments or []))
        return f"Departments: {names}" + (" (and sub-units)" if include_children else "")
    if audience == Audience.GROUPS:
        return "Groups: " + ", ".join(sorted(g.name for g in groups or []))
    names = sorted(describe(u) for u in employees or [])
    if len(names) > 6:
        return f"{len(names)} selected employees"
    return "Employees: " + ", ".join(names)


# ---------------------------------------------------------------------------
# Setting the audience, and broadcasting
# ---------------------------------------------------------------------------
def can_broadcast(user, circular):
    """
    Who may define an audience and send.

    The author, the issuer, HR and Admin. Deliberately NOT every reviewer: reviewing
    the text is not the same authority as deciding who reads it.
    """
    if circular.status in Circular.TERMINAL_STATUSES:
        return False
    if not circular.is_official:
        return False
    return (_is_admin(user) or _is_hr(user)
            or circular.created_by_id == user.id
            or circular.issued_by_id == user.id)


@transaction.atomic
def broadcast(circular, actor, *, audience, department_ids=None, group_ids=None,
              employee_ids=None, include_children=True, remarks="", request=None):
    """
    Materialise the audience and send.

    One call does both defining and sending, because a "ready for broadcast" state
    with an audience but no send is a state nobody can act on usefully - and the
    ladder already distinguishes ISSUED (no audience) from BROADCASTED (sent). The
    intermediate READY_FOR_BROADCAST is set and cleared inside this transaction so
    the status ladder stays honest without asking a user to press two buttons.
    """
    circular = Circular.objects.select_for_update().get(pk=circular.pk)
    if not circular.is_official:
        raise ValidationError({"broadcast": (
            f"This circular is {circular.get_status_display().lower()}. Only an "
            "issued circular can be broadcast - issuing is what makes it "
            "official.")})
    if circular.status == Status.ARCHIVED:
        raise ValidationError({"broadcast": (
            "This circular is archived. Extend the audience before archiving, or "
            "issue an amended circular.")})
    if not can_broadcast(actor, circular):
        raise PermissionDenied(
            "Only the author, the issuer, HR or an administrator can broadcast.")

    from leaves.models import Department
    departments = list(Department.objects.filter(pk__in=department_ids or []))
    groups = list(Group.objects.filter(pk__in=group_ids or []))
    employees = list(User.objects.filter(pk__in=employee_ids or []))
    if audience == Audience.DEPARTMENTS and len(departments) != len(department_ids or []):
        raise ValidationError({"department_ids": "One or more departments do not exist."})
    if audience == Audience.GROUPS and len(groups) != len(group_ids or []):
        raise ValidationError({"group_ids": "One or more groups do not exist."})
    if audience == Audience.EMPLOYEES and len(employees) != len(employee_ids or []):
        raise ValidationError({"employee_ids": "One or more employees do not exist."})

    people = resolve_audience(
        audience=audience, department_ids=[d.pk for d in departments],
        group_ids=[g.pk for g in groups], employee_ids=[u.pk for u in employees],
        include_children=include_children, exclude_user=circular.issued_by,
        category=circular.category)
    if not people.exists():
        raise ValidationError({"audience": (
            "That audience resolves to nobody. Check the departments or groups "
            "named - broadcasting to an empty audience would report a delivery "
            "that did not happen.")})

    # Mark the intermediate state so the ladder is honest even though the user
    # pressed one button.
    circular.status = Status.READY_FOR_BROADCAST
    circular.save(update_fields=["status", "updated_at"])
    record_audit(circular, actor, Action.AUDIENCE_SET,
                 remarks=describe_audience(
                     audience=audience, departments=departments, groups=groups,
                     employees=employees, include_children=include_children),
                 metadata={"audience": audience}, request=request)

    sequence = (circular.broadcasts.count() or 0) + 1
    event = CircularBroadcast.objects.create(
        circular=circular, sequence=sequence, audience=audience,
        include_child_departments=include_children,
        audience_label=describe_audience(
            audience=audience, departments=departments, groups=groups,
            employees=employees, include_children=include_children),
        broadcast_by=actor, broadcast_by_name=describe(actor),
        remarks=(remarks or "").strip())
    event.departments.set(departments)
    event.groups.set(groups)
    event.employees.set(employees)

    added, already = _materialise(circular, event, people)

    event.recipient_count = added
    event.already_present_count = already
    event.save(update_fields=["recipient_count", "already_present_count"])

    circular.status = Status.BROADCASTED
    if circular.broadcast_at is None:
        # First broadcast only. An extension must not move the acknowledgement
        # deadline of the people who already had it.
        circular.broadcast_at = timezone.now()
    circular.save(update_fields=["status", "broadcast_at", "updated_at"])

    record_audit(circular, actor, Action.BROADCAST,
                 remarks=f"{event.audience_label} — {added} recipient(s)"
                         + (f", {already} already had it" if already else ""),
                 metadata={"broadcast": sequence, "recipients": added,
                           "already_present": already, "audience": audience},
                 request=request)
    _notify_recipients(circular, event)
    return event


def _materialise(circular, event, people):
    """
    Create the recipient rows, plus acknowledgement rows if the circular needs them.

    `bulk_create` with `ignore_conflicts` rather than a per-person existence check:
    the unique constraint on (circular, user) is the authority, and letting the
    database enforce it turns an N-query loop into one statement. The count of who
    was already present is then a difference rather than a second pass.
    """
    existing = set(circular.recipients.values_list("user_id", flat=True))
    fresh = [person for person in people if person.pk not in existing]

    CircularRecipient.objects.bulk_create(stamp_all([
        CircularRecipient(circular=circular, broadcast=event, user=person,
                          **snapshot(person))
        for person in fresh
    ]), ignore_conflicts=True, batch_size=500)

    created = list(circular.recipients.filter(broadcast=event))
    if circular.acknowledgement_required:
        # The circular's own deadline if the round has already opened (an
        # extension), otherwise one computed from today (the first broadcast, where
        # `broadcast_at` is still unset when this runs). Snapshotted onto each row
        # so a later change to the due-days setting cannot move a deadline somebody
        # was already given.
        due = circular.acknowledgement_deadline
        if due is None:
            due = (timezone.localdate()
                   + datetime.timedelta(days=circular.acknowledgement_due_days))
        CircularAcknowledgement.objects.bulk_create(stamp_all([
            CircularAcknowledgement(recipient=row, due_date=due)
            for row in created
        ]), ignore_conflicts=True, batch_size=500)

    return len(created), len(people) - len(fresh)


def _notify_recipients(circular, event):
    """
    Tell the people this broadcast added.

    Best-effort and per-recipient: one failed delivery must not roll back a
    broadcast that has already been recorded, and the recipient rows are the
    authoritative record of who was reached regardless.
    """
    from .workflow import _notify

    category = ("CIRCULAR_ACK_REQUIRED" if circular.acknowledgement_required
                else "CIRCULAR_EXECUTIVE" if circular.category == Circular.Category.EXECUTIVE
                else "CIRCULAR_BROADCAST")
    body = f"{circular.get_category_display()}: {circular.subject}"
    if circular.acknowledgement_required:
        body += ("\n\nThis circular requires your acknowledgement. Opening it is "
                 "not the same as acknowledging it.")
    stamped = []
    for recipient in circular.recipients.filter(broadcast=event).select_related("user"):
        _notify(recipient.user, category,
                f"Circular {circular.circular_number}", body, circular)
        stamped.append(recipient.pk)
    CircularRecipient.objects.filter(pk__in=stamped).update(
        notified_at=timezone.now())


# ---------------------------------------------------------------------------
# Read tracking
# ---------------------------------------------------------------------------
def record_read(circular, user):
    """
    Note that `user` has opened the circular.

    Called from the detail endpoint, so it must be cheap and must never fail the
    read: somebody opening a circular should see it whether or not the tracker
    could be written. Returns the log row, or None when the caller is not a
    recipient - the author and the issuer can open a circular without being in its
    audience, and counting them would make every percentage wrong.
    """
    recipient = circular.recipients.filter(user=user).first()
    if recipient is None:
        return None

    now = timezone.now()
    log = getattr(recipient, "read_log", None)
    if log is None:
        try:
            log = CircularReadLog.objects.create(
                recipient=recipient, opened_at=now, last_viewed_at=now,
                view_count=1)
        except Exception:  # pragma: no cover - a race with a second tab
            log = CircularReadLog.objects.filter(recipient=recipient).first()
            if log is None:
                return None
        else:
            # Audited once, on FIRST open only. Auditing every view would write a
            # row per page refresh, which is why READ is in HIGH_VOLUME_ACTIONS
            # and excluded from the timeline even so.
            record_audit(circular, user, Action.READ,
                         remarks=f"{describe(user)} opened the circular.")
            return log

    # opened_at is never touched again - it is the FIRST open by definition.
    CircularReadLog.objects.filter(pk=log.pk).update(
        last_viewed_at=now, view_count=log.view_count + 1)
    return log


def read_summary(circular):
    """
    Read statistics. Three COUNT queries regardless of audience size.

    Unread is derived as total minus read rather than counted separately, because
    "no read log" is what unread MEANS - see CircularReadLog's docstring on why the
    absence of a row is the representation.
    """
    totals = circular.recipients.aggregate(
        total=Count("id"),
        read=Count("read_log"),
    )
    total = totals["total"] or 0
    read = totals["read"] or 0
    return {
        "total": total,
        "read": read,
        "unread": total - read,
        "percent": round(read * 100 / total) if total else 0,
    }


def read_register(circular, limit=None):
    """Per-recipient read state, for the detail page and the PDF."""
    rows = circular.recipients.select_related("user", "read_log").order_by("user_name")
    if limit:
        rows = rows[:limit]
    register = []
    for row in rows:
        log = getattr(row, "read_log", None)
        register.append({
            "id": str(row.id),
            "user_id": str(row.user_id) if row.user_id else None,
            "name": row.display_name,
            "designation": row.designation,
            "department": row.department_label,
            "state": "read" if log else "unread",
            "opened_at": log.opened_at if log else None,
            "last_viewed_at": log.last_viewed_at if log else None,
            "view_count": log.view_count if log else 0,
        })
    return register


# ---------------------------------------------------------------------------
# Acknowledgement
# ---------------------------------------------------------------------------
def acknowledgement_summary(circular):
    """
    The tally. ONE definition of each word, used everywhere.

    The minute module's audit found two summaries with two meanings of "pending"
    rendered side by side, reporting eleven states for six people. So: the four
    states here are EXCLUSIVE and sum to the total, and `late` is reported
    separately as an overlay on pending and acknowledged rather than as a fifth
    bucket - because somebody who acknowledged late is both acknowledged and late,
    and pretending otherwise is what caused that defect.
    """
    if not circular.acknowledgement_required:
        return {"required": False, "total": 0, "pending": 0, "acknowledged": 0,
                "declined": 0, "late": 0, "percent": 0, "is_complete": False,
                "deadline": None}

    rows = list(CircularAcknowledgement.objects.filter(
        recipient__circular=circular).only("state", "responded_at", "due_date"))
    total = len(rows)
    counts = {AckState.PENDING: 0, AckState.ACKNOWLEDGED: 0, AckState.DECLINED: 0}
    late = 0
    for row in rows:
        counts[row.state] = counts.get(row.state, 0) + 1
        if row.is_late:
            late += 1
    acknowledged = counts[AckState.ACKNOWLEDGED]
    return {
        "required": True,
        "total": total,
        "pending": counts[AckState.PENDING],
        "acknowledged": acknowledged,
        "declined": counts[AckState.DECLINED],
        # An OVERLAY, not a bucket: a late acknowledgement is already counted in
        # `acknowledged`. The four buckets above sum to `total`; this does not.
        "late": late,
        "percent": round(acknowledged * 100 / total) if total else 0,
        "is_complete": total > 0 and counts[AckState.PENDING] == 0,
        "deadline": circular.acknowledgement_deadline,
    }


def acknowledgement_register(circular):
    """Per-recipient acknowledgement state, for the panel and the PDF."""
    rows = CircularAcknowledgement.objects.filter(
        recipient__circular=circular).select_related(
            "recipient", "recipient__user").order_by("recipient__user_name")
    return [{
        "id": str(row.id),
        "recipient_id": str(row.recipient_id),
        "name": row.recipient.display_name,
        "designation": row.recipient.designation,
        "department": row.recipient.department_label,
        "state": row.state,
        "state_label": row.get_state_display(),
        "remarks": row.remarks,
        "responded_at": row.responded_at,
        "due_date": row.due_date,
        "is_late": row.is_late,
        "reminded_at": row.reminded_at,
    } for row in rows]


@transaction.atomic
def acknowledge(circular, user, *, accept=True, remarks="", request=None):
    """
    Record the caller's acknowledgement, or their decline.

    A DECLINE requires a reason and an ACCEPT does not: "I have read this" needs no
    explanation, "I have read this and do not accept it" is only useful to whoever
    reads the register if it says why.
    """
    if not circular.acknowledgement_required:
        raise ValidationError({"acknowledgement": (
            "This circular does not ask for an acknowledgement.")})
    if circular.status not in (Status.BROADCASTED, Status.ARCHIVED):
        raise ValidationError({"acknowledgement": (
            "This circular has not been broadcast yet.")})

    recipient = circular.recipients.filter(user=user).first()
    if recipient is None:
        raise ValidationError({"acknowledgement": (
            "You are not a recipient of this circular.")})
    row = CircularAcknowledgement.objects.select_for_update().filter(
        recipient=recipient).first()
    if row is None:
        raise ValidationError({"acknowledgement": (
            "No acknowledgement was requested from you.")})
    if row.state != AckState.PENDING:
        raise ValidationError({"acknowledgement": (
            f"You have already responded ({row.get_state_display().lower()}). An "
            "acknowledgement is a statement of fact and is not revised.")})

    remarks = (remarks or "").strip()
    if not accept and len(remarks) < MIN_REMARK_LENGTH:
        raise ValidationError({"remarks": (
            f"A decline needs a reason of at least {MIN_REMARK_LENGTH} "
            "characters - it is the only thing the register can report.")})

    row.state = AckState.ACKNOWLEDGED if accept else AckState.DECLINED
    row.remarks = remarks
    row.responded_at = timezone.now()
    row.save(update_fields=["state", "remarks", "responded_at"])

    record_audit(circular, user,
                 Action.ACKNOWLEDGED if accept else Action.DECLINED,
                 remarks=remarks,
                 metadata={"late": row.is_late}, request=request)
    return row


@transaction.atomic
def remind_acknowledgements(circular, actor, request=None):
    """Re-notify everyone whose acknowledgement is still outstanding."""
    if not circular.acknowledgement_required:
        return 0
    if not (can_broadcast(actor, circular) or _is_admin(actor) or _is_hr(actor)
            or circular.created_by_id == actor.id):
        raise PermissionDenied("You cannot send reminders on this circular.")

    from .workflow import _notify
    outstanding = list(CircularAcknowledgement.objects.filter(
        recipient__circular=circular, state=AckState.PENDING).select_related(
            "recipient", "recipient__user"))
    for row in outstanding:
        _notify(row.recipient.user, "CIRCULAR_ACK_REMINDER",
                f"Reminder: acknowledge circular {circular.circular_number}",
                f"'{circular.subject}' is still awaiting your acknowledgement"
                + (f", due {row.due_date}." if row.due_date else "."),
                circular)
    CircularAcknowledgement.objects.filter(
        pk__in=[row.pk for row in outstanding]).update(reminded_at=timezone.now())
    if outstanding:
        record_audit(circular, actor, Action.REMINDED,
                     remarks=f"Reminded {len(outstanding)} recipient(s).",
                     metadata={"count": len(outstanding)}, request=request)
    return len(outstanding)


def broadcast_history(circular):
    """Every broadcast event, for the detail payload and the PDF."""
    return [{
        "id": str(event.id),
        "sequence": event.sequence,
        "audience": event.audience,
        "audience_label": event.audience_label,
        "audience_kind": event.get_audience_display(),
        "recipient_count": event.recipient_count,
        "already_present_count": event.already_present_count,
        "broadcast_by": event.broadcast_by_name,
        "broadcast_at": event.broadcast_at,
        "remarks": event.remarks,
    } for event in circular.broadcasts.all()]
