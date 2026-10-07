"""
The minute state machine, as the E-minute manual defines it.

    Draft ──send_for_draft_review──▶ Draft For Review ──return_from_review──▶ Draft
      │                                     │
      └──────────send_for_acknowledgement───┘
                          │
                          ▼
            Pending Acknowledgement ──every present member acknowledged──▶ Archived

Four states, three transitions, one automatic one. That is the whole engine; there is
no approval chain, because the manual has none (see minutes/models.py).

TRANSITIONS ARE THE ONLY WAY STATUS CHANGES
-------------------------------------------
Every function here is atomic and writes its audit row in the same transaction as the
state change, so a rolled-back transition cannot leave a trail claiming it happened.
Nothing outside this module assigns to `minute.status`.

OTP IS DELIBERATELY ABSENT
--------------------------
The manual sends a one-time password to the acknowledger's email before recording an
acknowledgement (p.9). That step is excluded by instruction. `acknowledge()` records
directly, and every other property of the acknowledgement - who is asked, who has
answered, when, the signature stamps, the audit row - is unchanged by its absence.
"""
import datetime
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from notifications.models import Category

from .models import Minute, MinuteAttachment, MinuteParticipant
from .services import (
    notify_user, record_audit, user_snapshot, validate_minute_attachment,
)
from tenancy.stamping import stamp_all

logger = logging.getLogger("minutes")


def _audit_action(name):
    """
    Resolve an audit action by name at call time.

    Imported lazily rather than at module load: workflow is imported from models'
    consumers and a top-level import of MinuteAuditLog here reintroduces a cycle.
    """
    from .models import MinuteAuditLog
    return getattr(MinuteAuditLog.Action, name)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
def record_creation(minute, actor, request=None):
    """
    Write the "Created" audit row. Idempotent, so calling it twice cannot double the
    row - the timeline's first event is the one every later event depends on.
    """
    from .models import MinuteAuditLog
    if minute.audit_entries.filter(action=MinuteAuditLog.Action.CREATED).exists():
        return None
    return record_audit(minute, actor, MinuteAuditLog.Action.CREATED,
                        remarks="Minute created.", request=request)


# ---------------------------------------------------------------------------
# Participants (Members Present / Absent / Invitee - manual p.4)
# ---------------------------------------------------------------------------
@transaction.atomic
def set_participants(minute, rows, actor, request=None):
    """
    Replace the participant list.

    Refused once the acknowledgement round is open: adding a present member then would
    change the denominator of a tally people have already answered, and could un-archive
    a minute that had been completed. The manual freezes the list at "Submit for
    Acknowledge" by putting the pickers only on the draft form.
    """
    if minute.status not in Minute.EDITABLE_STATUSES:
        raise ValidationError(
            {"participants": "The participant list can only be changed while the "
                             "minute is a draft."})

    seen = set()
    resolved = []
    for index, row in enumerate(rows, start=1):
        user = row.get("user")
        if user is None:
            raise ValidationError(
                {"participants": f"Row {index}: the selected employee does not exist."})
        if user.id in seen:
            raise ValidationError(
                {"participants": f"{user.get_full_name() or user.username} appears "
                                 "more than once."})
        seen.add(user.id)
        resolved.append((user, row.get("attendance",
                                       MinuteParticipant.Attendance.PRESENT)))

    minute.participants.all().delete()
    created = []
    for user, attendance in resolved:
        snapshot = user_snapshot(user)
        created.append(MinuteParticipant(
            minute=minute, user=user,
            user_name=snapshot["name"], designation=snapshot["designation"],
            department_label=snapshot["department"],
            attendance=attendance,
            ack_status=MinuteParticipant.AckStatus.NOT_REQUIRED,
        ))
    MinuteParticipant.objects.bulk_create(stamp_all(created))

    record_audit(minute, actor, _audit_action("PARTICIPANTS_CHANGED"),
                 remarks=f"{len(created)} participant(s) recorded.",
                 metadata={"count": len(created)}, request=request)
    return minute.participants.all()


# ---------------------------------------------------------------------------
# Submit for Draft Review (manual p.7)
# ---------------------------------------------------------------------------
@transaction.atomic
def send_for_draft_review(minute, actor, request=None):
    """
    Send the draft to the FRO for review.

    The manual's "Submit for Draft Review" button. The destination is the FRO field on
    the form - "First reporting officer to access the meeting minute" (p.4) - so a
    minute with no FRO has nowhere to send a review and the transition is refused
    rather than silently doing nothing.
    """
    if minute.status != Minute.Status.DRAFT:
        raise ValidationError(
            {"workflow": "Only a draft can be submitted for review."})
    if minute.fro_id is None:
        raise ValidationError(
            {"workflow": "Choose an FRO before submitting for draft review."})

    minute.status = Minute.Status.DRAFT_FOR_REVIEW
    minute.sent_for_review_at = timezone.now()
    minute.save(update_fields=["status", "sent_for_review_at", "updated_at"])

    record_audit(minute, actor, _audit_action("SENT_FOR_REVIEW"),
                 remarks=f"Sent to {minute.fro_name or 'the FRO'} for draft review.",
                 request=request)
    notify_user(minute.fro, Category.MINUTE_REVIEW_REQUIRED,
                "Minute draft to review",
                f"{minute.minute_number} is with you for draft review.", minute)
    return minute


@transaction.atomic
def return_from_review(minute, actor, remarks="", request=None):
    """
    The FRO hands the draft back to the initiator.

    The manual does not name this button, but it does put the minute in front of a
    reviewer, and a review with no way to say "not yet" is not a review. It returns to
    Draft - the state the initiator can edit - rather than inventing a rejected status.
    """
    if minute.status != Minute.Status.DRAFT_FOR_REVIEW:
        raise ValidationError(
            {"workflow": "Only a minute under draft review can be returned."})

    minute.status = Minute.Status.DRAFT
    minute.sent_for_review_at = None
    minute.save(update_fields=["status", "sent_for_review_at", "updated_at"])

    record_audit(minute, actor, _audit_action("REVIEW_RETURNED"),
                 remarks=remarks or "Returned to the initiator.", request=request)
    notify_user(minute.created_by, Category.MINUTE_REJECTED,
                "Minute returned",
                f"{minute.minute_number} was returned to you after draft review.",
                minute)
    return minute


# ---------------------------------------------------------------------------
# Submit for Acknowledge (manual pp. 7-8)
# ---------------------------------------------------------------------------
@transaction.atomic
def send_for_acknowledgement(minute, actor, request=None):
    """
    Open the acknowledgement round: "the minute will be assigned to the members
    present in the meeting" (p.8).

    Reachable from a plain draft as well as from one that has been through draft
    review, because the manual puts both buttons side by side on the draft (p.7) -
    review is optional.

    Only members PRESENT are asked. Absent members and invitees keep NOT_REQUIRED and
    appear on the signature sheet without being chased.
    """
    if minute.status not in (Minute.Status.DRAFT, Minute.Status.DRAFT_FOR_REVIEW):
        raise ValidationError(
            {"workflow": "This minute has already been submitted for acknowledgement."})

    present = [p for p in minute.participants.all()
               if p.attendance == MinuteParticipant.Attendance.PRESENT]
    if not present:
        raise ValidationError(
            {"workflow": "Add at least one member present before submitting for "
                         "acknowledgement - they are who is asked to acknowledge."})

    now = timezone.now()
    minute.status = Minute.Status.PENDING_ACKNOWLEDGEMENT
    minute.acknowledgement_opened_at = now
    minute.save(update_fields=["status", "acknowledgement_opened_at", "updated_at"])

    for participant in present:
        participant.ack_status = MinuteParticipant.AckStatus.PENDING
    MinuteParticipant.objects.bulk_update(present, ["ack_status"])

    record_audit(minute, actor, _audit_action("SENT_FOR_ACK"),
                 remarks=f"Assigned to {len(present)} member(s) present.",
                 metadata={"present": len(present)}, request=request)
    for participant in present:
        notify_user(participant.user, Category.MINUTE_ACK_REQUIRED,
                    "Minute to acknowledge",
                    f"{minute.minute_number} is waiting for your acknowledgement.",
                    minute)
    return minute


# ---------------------------------------------------------------------------
# Acknowledging (manual pp. 8-9, minus the OTP)
# ---------------------------------------------------------------------------
@transaction.atomic
def acknowledge(minute, user, remarks="", request=None):
    """
    Record one member's acknowledgement, and archive the minute once they are all in.

    "Once a minute is acknowledged by all present members, the minute will be archived"
    (p.10) - so archival is a consequence of the last acknowledgement, computed here,
    never a button somebody presses.
    """
    if minute.status != Minute.Status.PENDING_ACKNOWLEDGEMENT:
        raise ValidationError(
            {"acknowledgement": "This minute is not open for acknowledgement."})

    participant = next((p for p in minute.participants.all()
                        if p.user_id == getattr(user, "id", None)), None)
    if participant is None:
        raise ValidationError(
            {"acknowledgement": "You are not a participant on this minute."})
    if not participant.must_acknowledge:
        raise ValidationError(
            {"acknowledgement": "Only members recorded as present acknowledge a "
                                "minute."})
    if participant.ack_status == MinuteParticipant.AckStatus.ACKNOWLEDGED:
        raise ValidationError(
            {"acknowledgement": "You have already acknowledged this minute."})

    participant.ack_status = MinuteParticipant.AckStatus.ACKNOWLEDGED
    participant.acknowledged_at = timezone.now()
    participant.remarks = (remarks or "").strip()
    participant.save(update_fields=["ack_status", "acknowledged_at", "remarks"])

    record_audit(minute, user, _audit_action("ACKNOWLEDGED"),
                 remarks=participant.remarks, request=request)
    notify_user(minute.created_by, Category.MINUTE_ACKNOWLEDGED,
                "Minute acknowledged",
                f"{participant.display_name} acknowledged {minute.minute_number}.",
                minute)

    # Re-read the rows through the manager: `minute.participants.all()` may be a
    # prefetched cache holding the pre-save copy of the row just updated, which would
    # make the last acknowledgement look outstanding and skip archival.
    minute.refresh_from_db()
    if _all_present_acknowledged(minute):
        _archive(minute, user, request=request)
    return participant


def _all_present_acknowledged(minute):
    present = minute.participants.filter(
        attendance=MinuteParticipant.Attendance.PRESENT)
    if not present.exists():
        return False
    return not present.exclude(
        ack_status=MinuteParticipant.AckStatus.ACKNOWLEDGED).exists()


def _archive(minute, actor, request=None):
    minute.status = Minute.Status.ARCHIVED
    minute.archived_at = timezone.now()
    minute.save(update_fields=["status", "archived_at", "updated_at"])
    record_audit(minute, actor, _audit_action("ARCHIVED"),
                 remarks="All members present have acknowledged; minute archived.",
                 request=request)
    notify_user(minute.created_by, Category.MINUTE_ARCHIVED,
                "Minute archived",
                f"{minute.minute_number} has been acknowledged by every member "
                "present and is now archived.", minute)
    return minute


@transaction.atomic
def remind_pending_acknowledgements(minute, actor, request=None):
    """Re-notify everyone still outstanding. Records who was chased and when."""
    if minute.status != Minute.Status.PENDING_ACKNOWLEDGEMENT:
        raise ValidationError(
            {"acknowledgement": "There is no open acknowledgement round to remind."})

    pending = list(minute.participants.filter(
        attendance=MinuteParticipant.Attendance.PRESENT,
        ack_status=MinuteParticipant.AckStatus.PENDING))
    now = timezone.now()
    for participant in pending:
        participant.reminded_at = now
        notify_user(participant.user, Category.MINUTE_ACK_REMINDER,
                    "Minute acknowledgement reminder",
                    f"{minute.minute_number} is still waiting for your "
                    "acknowledgement.", minute)
    MinuteParticipant.objects.bulk_update(pending, ["reminded_at"])
    record_audit(minute, actor, _audit_action("ACK_REMINDED"),
                 remarks=f"Reminded {len(pending)} member(s).",
                 metadata={"reminded": len(pending)}, request=request)
    return len(pending)


# ---------------------------------------------------------------------------
# Read models for the API
# ---------------------------------------------------------------------------
def acknowledgement_summary(minute):
    """The tally the detail page and the list column both read."""
    present = [p for p in minute.participants.all()
               if p.attendance == MinuteParticipant.Attendance.PRESENT]
    acknowledged = [p for p in present
                    if p.ack_status == MinuteParticipant.AckStatus.ACKNOWLEDGED]
    total = len(present)
    return {
        "total": total,
        "acknowledged": len(acknowledged),
        "pending": total - len(acknowledged),
        "is_open": minute.status == Minute.Status.PENDING_ACKNOWLEDGEMENT,
        "is_complete": bool(total) and len(acknowledged) == total,
        "opened_at": minute.acknowledgement_opened_at,
        "percent": round(len(acknowledged) * 100 / total) if total else 0,
    }


def acknowledgement_deadline_summary(minute):
    """Deadline plus the late tally, derived from when the round opened."""
    opened = minute.acknowledgement_opened_at
    present = [p for p in minute.participants.all()
               if p.attendance == MinuteParticipant.Attendance.PRESENT]
    late = [p for p in present if p.is_late]
    due = None
    if opened:
        due = (opened + datetime.timedelta(
            days=minute.acknowledgement_due_days)).date()
    return {
        "due_date": due,
        "due_days": minute.acknowledgement_due_days,
        "late": len(late),
        "is_overdue": bool(late),
    }


def signature_blocks(minute):
    """
    The signature strip under the minute (pp. 8-9).

    One block per participant, stamped ACKNOWLEDGED for those who have confirmed and
    ABSENT for those recorded absent - exactly the two stamps the manual shows. An
    invitee gets a block with no stamp.
    """
    blocks = []
    for participant in minute.participants.all():
        if participant.attendance == MinuteParticipant.Attendance.ABSENT:
            stamp = "ABSENT"
        elif participant.ack_status == MinuteParticipant.AckStatus.ACKNOWLEDGED:
            stamp = "ACKNOWLEDGED"
        else:
            stamp = ""
        blocks.append({
            "name": participant.display_name,
            "designation": participant.designation,
            "department": participant.department_label,
            "attendance": participant.attendance,
            "attendance_label": participant.get_attendance_display(),
            "stamp": stamp,
            "date": participant.acknowledged_at,
        })
    return blocks


# The PDF template reads the same rows; one function, two renderers.
signature_rows = signature_blocks


def build_tracker(minute):
    """
    The three stages of a minute's life, for the progress strip.

    Draft review is shown only when the minute actually has an FRO, so a minute that
    never needed one does not display a stage it will never enter.
    """
    order = [Minute.Status.DRAFT, Minute.Status.DRAFT_FOR_REVIEW,
             Minute.Status.PENDING_ACKNOWLEDGEMENT, Minute.Status.ARCHIVED]
    position = order.index(minute.status)

    def state(status):
        index = order.index(status)
        if index < position:
            return "done"
        if index > position:
            return "pending"
        # Archived is terminal: the minute is not sitting AT that stage waiting to
        # move on, it has finished. Reporting the last stage as "active" made a
        # completed minute read as though something were still in progress.
        return "done" if status == Minute.Status.ARCHIVED else "active"

    stages = [{"key": "draft", "label": "Draft", "state": state(Minute.Status.DRAFT)}]
    if minute.fro_id or minute.status == Minute.Status.DRAFT_FOR_REVIEW:
        stages.append({"key": "review", "label": "Draft Review",
                       "state": state(Minute.Status.DRAFT_FOR_REVIEW)})
    stages.append({"key": "acknowledgement", "label": "Acknowledgement",
                   "state": state(Minute.Status.PENDING_ACKNOWLEDGEMENT)})
    stages.append({"key": "archived", "label": "Archived",
                   "state": state(Minute.Status.ARCHIVED)})
    return stages


def build_timeline(minute):
    """Every audit row, oldest first - what the History panel renders."""
    return [
        {
            "id": str(entry.id),
            "action": entry.action,
            "label": entry.get_action_display(),
            # `actor_name` rather than `actor`: this feeds the shared WorkflowTimeline
            # component, which reads that key. Sending our own name for it rendered a
            # timeline of blank actors.
            "actor_name": entry.actor_display,
            "remarks": entry.remarks,
            "at": entry.created_at,
        }
        for entry in minute.audit_entries.all()
    ]


def pending_with(minute):
    """Whose turn it is, in one line - or None when nobody owes anything."""
    if minute.status == Minute.Status.DRAFT:
        return {"name": minute.created_by.get_full_name() or
                minute.created_by.username, "role_label": "Initiator"}
    if minute.status == Minute.Status.DRAFT_FOR_REVIEW:
        return {"name": minute.fro_name or "—", "role_label": "FRO"}
    if minute.status == Minute.Status.PENDING_ACKNOWLEDGEMENT:
        outstanding = [p for p in minute.participants.all()
                       if p.must_acknowledge
                       and p.ack_status != MinuteParticipant.AckStatus.ACKNOWLEDGED]
        if outstanding:
            names = ", ".join(p.display_name for p in outstanding[:2])
            more = len(outstanding) - 2
            return {"name": names + (f" +{more}" if more > 0 else ""),
                    "role_label": "To acknowledge"}
    return None


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------
@transaction.atomic
def add_attachment(minute, actor, uploaded, replaces=None, request=None):
    """Attach a file, superseding an earlier version rather than overwriting it."""
    validate_minute_attachment(uploaded)
    version = 1
    if replaces is not None:
        version = replaces.version + 1
        replaces.is_current = False
        replaces.save(update_fields=["is_current"])
    row = MinuteAttachment.objects.create(
        minute=minute, file=uploaded,
        original_name=getattr(uploaded, "name", "")[:255],
        content_type=getattr(uploaded, "content_type", "")[:100],
        size=getattr(uploaded, "size", 0) or 0,
        version=version, replaces=replaces, uploaded_by=actor,
    )
    record_audit(minute, actor, _audit_action("UPDATED"),
                 remarks=f"Attached {row.original_name}.", request=request)
    return row


@transaction.atomic
def remove_attachment(minute, actor, attachment, request=None):
    name = attachment.original_name
    attachment.delete()
    record_audit(minute, actor, _audit_action("UPDATED"),
                 remarks=f"Removed attachment {name}.", request=request)
