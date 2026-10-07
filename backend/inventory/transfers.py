"""
Asset custody transfer workflow (Phase ASSET-CUSTODY-TRANSFER).

    DRAFT -> HR_REVIEW -> COMPLETED
                \\__________-> REJECTED
    (any open state, by the requester) -> CANCELLED

HR IS THE FINAL AUTHORITY (Phase ASSET-TRANSFER-GOVERNANCE). The department-head
and administrator gates are gone. See AssetTransfer.REVIEW_STAGES for why their
status values survive, and migration 0011 for what happened to the transfers that
were sitting at them.

WHAT THIS MODULE DOES NOT DO
----------------------------
It never writes custody itself. On final approval it calls
`services.handover_item`, which is still the one piece of code that closes an
ItemAssignment and opens the next. A transfer is the governance in front of that
change; keeping the change itself in one place is what guarantees the register
and the transfer can never disagree about who holds an asset.

DESIGN RULES, inherited from the rest of the inventory lifecycle
----------------------------------------------------------------
  * every transition is one `transaction.atomic` block, re-reading the transfer
    under `select_for_update()` so two approvers cannot both advance it;
  * a failed guard is a DRF ValidationError/PermissionDenied - a 400/403 with a
    message a person can act on;
  * nothing changes without an append-only event row AND a shared audit row;
  * notifications are sent on commit, so nobody is told about a transition a
    later guard rolled back.
"""
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from audit.models import AuditLog
from audit.services import log_action
from users.models import User

from . import roles
from .lifecycle import MIN_REASON_LENGTH, _guard_actionable, record_event
from .models import (
    AssetLifecycleEvent, AssetTransfer, AssetTransferAttachment,
    AssetTransferEvent, InventoryItem, ItemAssignment,
)
from .services import _name, _next_seq

logger = logging.getLogger("inventory.transfers")

Status = AssetTransfer.Status
Action = AssetTransferEvent.Action
Event = AssetLifecycleEvent.Event

# Which event the gate writes when it approves, and where it sends the transfer.
# One entry, because there is one gate.
_APPROVAL = {
    Status.HR_REVIEW: (Action.HR_APPROVED, Status.COMPLETED),
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def generate_transfer_number():
    """TRF-2026-0001 for NIF, scoped to the Gregorian year as the brief says.

    Another tenant gets its prefix in front (<PREFIX>-TRF-2026-0001): the bare
    form carries no organisation, so two tenants would otherwise mint the same
    transfer number. NIF keeps the bare form -- see tenancy/numbering.py.
    """
    from tenancy import numbering
    from tenancy.scoping import active_organization

    organization = active_organization()
    year = timezone.localdate().year
    return numbering.format_number(
        "TRF", year=year, value=_next_seq("TRF", year, organization),
        organization=organization)


def _department_name(user):
    return (getattr(user, "department_name", None) or "") if user else ""


def _announce(fn, *args):
    """Notify once the transition has COMMITTED - see lifecycle._announce."""
    from . import notifications

    transaction.on_commit(lambda: getattr(notifications, fn)(*args))


def _lock(transfer_id):
    try:
        return AssetTransfer.objects.select_for_update().get(pk=transfer_id)
    except AssetTransfer.DoesNotExist:
        raise ValidationError({"transfer": "This transfer does not exist."})


def _record(transfer, action, actor=None, *, remarks="", from_status="",
            to_status="", metadata=None, audit=AuditLog.Action.UPDATE):
    """Append one event to the transfer and one row to the shared audit log."""
    last = (AssetTransferEvent.objects.filter(transfer=transfer)
            .order_by("-sequence").values_list("sequence", flat=True).first()) or 0
    AssetTransferEvent.objects.create(
        transfer=transfer, sequence=last + 1, action=action,
        from_status=from_status or "", to_status=to_status or "",
        actor=actor, actor_name=_name(actor), remarks=remarks or "",
        metadata=metadata or {})
    log_action(actor, audit, instance=transfer, changes={
        "transition": action, "transfer_number": transfer.transfer_number,
        "from_status": from_status, "to_status": to_status,
        "remarks": remarks or "", **(metadata or {})})
    logger.info("transfer %s action=%s actor=%s", transfer.transfer_number, action,
                getattr(actor, "id", None))


def _require_remarks(value, field="remarks"):
    value = (value or "").strip()
    if len(value) < MIN_REASON_LENGTH:
        raise ValidationError({field: (
            f"Remarks of at least {MIN_REASON_LENGTH} characters are required. "
            "They are the only record of why this decision was made.")})
    return value


def current_holder(item):
    """The active custody row for an asset, or None."""
    return (ItemAssignment.objects.filter(item=item, is_active=True)
            .select_related("assigned_to").first())


# --------------------------------------------------------------------------- #
# Authority
# --------------------------------------------------------------------------- #
def can_create(user):
    """Whoever runs assets may raise a transfer. An employee cannot move their own."""
    return roles.can_manage_assets(user)


def is_party(user, transfer):
    """
    The people the transfer is ABOUT. They may never approve it: a control that
    the giver or the receiver of a laptop can satisfy is not a control.
    """
    uid = getattr(user, "id", None)
    return uid is not None and uid in (
        transfer.from_employee_id, transfer.to_employee_id, transfer.requested_by_id)


def heads_for_department(department_id):
    """
    The active heads who may release an asset from `department_id`.

    A giver with no recorded department has no specific head, so any head may act.
    One definition, used by both the authority check and the notifications, so the
    people asked to approve are always the people allowed to.
    """
    heads = User.objects.filter(role=User.Roles.CHECKER, is_active=True)
    return heads.filter(department_ref_id=department_id) if department_id else heads


def eligible_hr(transfer):
    """Active HR users who are not a party to this transfer."""
    parties = [pk for pk in (transfer.from_employee_id, transfer.to_employee_id,
                             transfer.requested_by_id) if pk]
    return User.objects.filter(role=User.Roles.APPROVER, is_active=True).exclude(pk__in=parties)


def can_act_at_stage(user, transfer, stage=None):
    """
    Whether `user` holds the authority for the gate `stage` (default: the current).

    HR, and nobody else. A Department Head cannot approve a transfer at all now -
    the brief makes them a reader - and there is no administrator gate left to
    hold.

    Admin still stands in when NO eligible HR user exists, and that is not a
    loophole left over from the old chain: HR may themselves be giving or
    receiving the asset, and without a stand-in that transfer would rest at a gate
    nobody on earth could clear. The condition is "nobody else can act", checked
    against the record, not "admin outranks HR".
    """
    stage = stage or transfer.status
    if not (user and user.is_authenticated) or is_party(user, transfer):
        return False
    if stage not in AssetTransfer.REVIEW_STAGES:
        # A transfer still resting at a retired gate (an older release left it
        # there and migration 0011 has not run). Nobody may decide it from here;
        # it has to be moved onto the HR gate first.
        return False
    if roles.is_hr(user):
        return True
    if roles.is_admin(user):
        return not eligible_hr(transfer).exists()
    return False


def visible_to(user):
    """
    The transfers a user may see.

      Employee                      - transfers they give, receive or raised
      Department Head, HR, Admin,
      Inventory Officer             - every transfer record

    The head's department scoping is gone (Phase ASSET-TRANSFER-GOVERNANCE). It
    existed to answer "authority needs visibility" while heads approved transfers
    out of their own department; they no longer approve anything, and the brief
    asks instead that they see the whole organisation. So the rule is now the
    plain one, and the awkward special case it used to need - showing a head
    transfers whose giver had no recorded department, so that a head notified to
    approve could actually find it - goes with it.
    """
    from django.db.models import Q

    qs = AssetTransfer.objects.all()
    if (roles.is_admin(user) or roles.is_hr(user) or roles.is_inventory_officer(user)
            or roles.is_department_head(user)):
        return qs
    # A party, or somebody who has already decided it: a decision you made stays
    # visible to you after the transfer moves on.
    return qs.filter(
        Q(from_employee=user) | Q(to_employee=user) | Q(requested_by=user)
        | Q(dept_head_by=user) | Q(hr_by=user) | Q(approved_by=user)
        | Q(rejected_by=user))


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #
def _validate_parties(item, to_employee):
    holder = current_holder(item)
    if holder is None or holder.assigned_to_id is None:
        raise ValidationError({"item": (
            f"{item.asset_code} is not assigned to anybody, so there is no custody "
            "to transfer. Assign it directly instead.")})
    if to_employee is None:
        raise ValidationError({"to_employee": (
            "Choose who receives the asset. To give it back to the store, raise an "
            "Asset Return instead.")})
    if not to_employee.is_active:
        raise ValidationError({"to_employee": (
            f"{_name(to_employee)} is no longer an active employee.")})
    if to_employee.id == holder.assigned_to_id:
        raise ValidationError({"to_employee": (
            f"{item.asset_code} is already held by {_name(to_employee)}.")})
    return holder


@transaction.atomic
def create_transfer(*, item, to_employee, requested_by, transfer_date, reason,
                    condition, remarks=""):
    if not can_create(requested_by):
        raise PermissionDenied("Only asset managers can raise a transfer.")
    item = InventoryItem.objects.select_for_update().get(pk=item.pk)
    _guard_actionable(item, "transferred")
    holder = _validate_parties(item, to_employee)
    giver = holder.assigned_to

    if AssetTransfer.objects.filter(item=item, status__in=AssetTransfer.OPEN_STATUSES).exists():
        existing = AssetTransfer.objects.filter(
            item=item, status__in=AssetTransfer.OPEN_STATUSES).first()
        raise ValidationError({"item": (
            f"{item.asset_code} already has transfer {existing.transfer_number} in "
            f"progress ({existing.get_status_display().lower()}). Finish or cancel "
            "that one first.")})

    try:
        with transaction.atomic():
            transfer = AssetTransfer.objects.create(
                transfer_number=generate_transfer_number(),
                item=item, item_code=item.asset_code, item_name=item.name,
                from_employee=giver, from_employee_name=_name(giver),
                from_department_name=_department_name(giver),
                to_employee=to_employee, to_employee_name=_name(to_employee),
                to_department_name=_department_name(to_employee),
                transfer_date=transfer_date, reason=reason, condition=condition,
                remarks=remarks or "", requested_by=requested_by,
                requested_by_name=_name(requested_by))
    except IntegrityError:
        # The partial unique constraint caught a race the check above lost.
        raise ValidationError({"item": (
            f"{item.asset_code} already has a transfer in progress.")})

    _record(transfer, Action.CREATED, requested_by, remarks=remarks,
            to_status=Status.DRAFT, audit=AuditLog.Action.CREATE,
            metadata={"asset": item.asset_code, "from": transfer.from_employee_name,
                      "to": transfer.to_employee_name, "reason": reason})
    return transfer


@transaction.atomic
def update_draft(transfer_id, actor, **fields):
    transfer = _lock(transfer_id)
    if transfer.status != Status.DRAFT:
        raise ValidationError({"status": (
            "Only a draft can be edited. Once submitted, a transfer is a record of "
            "what was approved.")})
    if actor.id != transfer.requested_by_id and not roles.is_admin(actor):
        raise PermissionDenied("Only the person who raised this transfer can edit it.")
    if "to_employee" in fields and fields["to_employee"] is not None:
        _validate_parties(transfer.item, fields["to_employee"])
        transfer.to_employee = fields["to_employee"]
        transfer.to_employee_name = _name(fields["to_employee"])
        transfer.to_department_name = _department_name(fields["to_employee"])
    for key in ("transfer_date", "reason", "condition", "remarks"):
        if key in fields:
            setattr(transfer, key, fields[key])
    transfer.save()
    return transfer


@transaction.atomic
def submit(transfer_id, actor):
    transfer = _lock(transfer_id)
    if transfer.status != Status.DRAFT:
        raise ValidationError({"status": (
            f"This transfer is {transfer.get_status_display().lower()}; only a draft "
            "can be submitted.")})
    if actor.id != transfer.requested_by_id and not roles.is_admin(actor):
        raise PermissionDenied("Only the person who raised this transfer can submit it.")
    if transfer.condition == AssetTransfer.Condition.LOST:
        raise ValidationError({"condition": (
            "A lost asset cannot be handed to another employee. Record the loss "
            "against the asset instead of transferring it.")})
    if transfer.item is None:
        raise ValidationError({"item": "This asset no longer exists."})
    # Custody may have moved since the draft was written. Re-check now rather than
    # let an approver spend time on a transfer that can no longer complete.
    holder = current_holder(transfer.item)
    if holder is None or holder.assigned_to_id != transfer.from_employee_id:
        raise ValidationError({"item": (
            f"{transfer.item_code} is no longer held by {transfer.from_employee_name}. "
            "Cancel this transfer and raise a new one.")})

    transfer.status = Status.HR_REVIEW
    transfer.submitted_at = timezone.now()
    transfer.save(update_fields=["status", "submitted_at", "updated_at"])
    _record(transfer, Action.SUBMITTED, actor, from_status=Status.DRAFT,
            to_status=Status.HR_REVIEW, audit=AuditLog.Action.SUBMIT)
    _announce("transfer_submitted", transfer)
    return transfer


@transaction.atomic
def approve(transfer_id, actor, *, remarks=""):
    transfer = _lock(transfer_id)
    if transfer.status not in AssetTransfer.REVIEW_STAGES:
        raise ValidationError({"status": (
            f"This transfer is {transfer.get_status_display().lower()} and is not "
            "awaiting approval.")})
    if is_party(actor, transfer):
        raise PermissionDenied(
            "You raised this transfer or are giving or receiving the asset, so you "
            "cannot decide it.")
    if not can_act_at_stage(actor, transfer):
        raise PermissionDenied(
            f"This transfer is at {transfer.get_status_display()}, which you are "
            "not authorised to decide.")

    stage = transfer.status
    action, next_status = _APPROVAL[stage]
    now = timezone.now()
    transfer.hr_by, transfer.hr_name = actor, _name(actor)
    transfer.hr_at, transfer.hr_remarks = now, remarks or ""
    # HR is the final authority, so HR's decision is also the approval of record.
    # Both sets of fields are written: `approved_by` is what the register, the
    # reports and every existing consumer read as "who approved this", and leaving
    # it empty would have made an approved transfer look unapproved everywhere
    # except the HR column.
    transfer.approved_by, transfer.approved_by_name = actor, _name(actor)
    transfer.approved_at = now

    # Approve and complete in ONE transaction: an approved transfer whose custody
    # change then failed would be a record that says an asset moved when it did not.
    transfer.save()
    _record(transfer, action, actor, remarks=remarks, from_status=stage,
            to_status=next_status, audit=AuditLog.Action.APPROVE)
    return _complete(transfer, actor)


def _complete(transfer, actor):
    """Execute the custody change. Called only from inside `approve`'s transaction."""
    from .services import handover_item

    item = InventoryItem.objects.select_for_update().get(pk=transfer.item_id) \
        if transfer.item_id else None
    if item is None:
        raise ValidationError({"item": "This asset no longer exists."})
    holder = (ItemAssignment.objects.select_for_update()
              .filter(item=item, is_active=True).first())
    if holder is None or holder.assigned_to_id != transfer.from_employee_id:
        raise ValidationError({"item": (
            f"{item.asset_code} is no longer held by {transfer.from_employee_name}, so "
            "this transfer cannot complete. Reject it and raise a new one.")})
    receiver = transfer.to_employee
    if receiver is None or not receiver.is_active:
        raise ValidationError({"to_employee": (
            f"{transfer.to_employee_name} is no longer an active employee.")})

    item_condition = AssetTransfer.CONDITION_TO_ITEM[transfer.condition]
    opened = handover_item(
        item.id, receiver, actor,
        note=f"Transfer {transfer.transfer_number} ({transfer.get_reason_display()})",
        assigned_date=transfer.transfer_date, handover_condition=item_condition,
        accessories=holder.accessories)
    # handover_item closed the old row; record the condition it left in, which is
    # the custody fact the giver is accountable for.
    holder.refresh_from_db()
    holder.return_condition = item_condition
    holder.return_remarks = f"Transferred under {transfer.transfer_number}"
    holder.save(update_fields=["return_condition", "return_remarks"])

    item.refresh_from_db()
    touched = []
    if item.condition != item_condition:
        old_condition = item.condition
        item.condition = item_condition
        touched.append("condition")
        record_event(item, Event.CONDITION_CHANGED, actor,
                     remarks=f"Recorded at transfer {transfer.transfer_number}",
                     from_status=item.status, to_status=item.status,
                     metadata={"from": old_condition, "to": item_condition,
                               "transfer_number": transfer.transfer_number})

    old_dept = item.department
    new_dept = getattr(receiver, "department_ref", None)
    department_changed = new_dept is not None and item.department_id != new_dept.id
    if department_changed:
        item.department = new_dept
        touched.append("department")
    if touched:
        item.save(update_fields=[*touched, "updated_at"])

    record_event(item, Event.TRANSFERRED, actor, remarks=transfer.remarks,
                 subject=transfer.to_employee_name,
                 from_status=item.status, to_status=item.status,
                 metadata={"transfer_number": transfer.transfer_number,
                           "from": transfer.from_employee_name,
                           "to": transfer.to_employee_name,
                           "reason": transfer.reason,
                           "condition": transfer.condition})
    _record(transfer, Action.OWNER_CHANGED, actor,
            metadata={"from": transfer.from_employee_name,
                      "to": transfer.to_employee_name,
                      "closed_assignment": str(holder.id),
                      "opened_assignment": str(opened.id)})

    if department_changed:
        record_event(item, Event.DEPARTMENT_CHANGED, actor,
                     remarks=f"Followed the new holder under {transfer.transfer_number}",
                     from_status=item.status, to_status=item.status,
                     metadata={"from": getattr(old_dept, "name", "") or "",
                               "to": new_dept.name,
                               "transfer_number": transfer.transfer_number})
        _record(transfer, Action.DEPARTMENT_CHANGED, actor,
                metadata={"from": getattr(old_dept, "name", "") or "",
                          "to": new_dept.name})

    transfer.status = Status.COMPLETED
    transfer.completed_at = timezone.now()
    transfer.closed_assignment = holder
    transfer.opened_assignment = opened
    transfer.save(update_fields=["status", "completed_at", "closed_assignment",
                                 "opened_assignment", "updated_at"])
    # OWNER CHANGED - the moment custody actually moved, recorded under its own
    # audit verb rather than the generic UPDATE this used to write. The brief asks
    # for "Owner Changed" as a distinct auditable event, and a compliance query
    # filters on the indexed `action` column: an ownership change that reads as an
    # edit is not findable without joining back to the events it was meant to
    # summarise.
    _record(transfer, Action.COMPLETED, actor, from_status=Status.HR_REVIEW,
            to_status=Status.COMPLETED, audit=AuditLog.Action.OWNERSHIP_CHANGED,
            metadata={"asset": transfer.item_code,
                      "from_owner": transfer.from_employee_name,
                      "to_owner": transfer.to_employee_name,
                      "closed_assignment": str(holder.id),
                      "opened_assignment": str(opened.id)})
    _announce("transfer_completed", transfer)
    return transfer


@transaction.atomic
def reject(transfer_id, actor, *, remarks):
    transfer = _lock(transfer_id)
    if transfer.status not in AssetTransfer.REVIEW_STAGES:
        raise ValidationError({"status": (
            f"This transfer is {transfer.get_status_display().lower()} and cannot be "
            "rejected.")})
    if is_party(actor, transfer):
        raise PermissionDenied(
            "You raised this transfer or are giving or receiving the asset, so you "
            "cannot decide it.")
    if not can_act_at_stage(actor, transfer):
        raise PermissionDenied(
            f"This transfer is at {transfer.get_status_display()}, which you are "
            "not authorised to decide.")
    remarks = _require_remarks(remarks)

    stage = transfer.status
    transfer.status = Status.REJECTED
    transfer.rejected_by, transfer.rejected_by_name = actor, _name(actor)
    transfer.rejected_at = timezone.now()
    transfer.rejected_stage = stage
    transfer.rejection_remarks = remarks
    transfer.save()
    _record(transfer, Action.REJECTED, actor, remarks=remarks, from_status=stage,
            to_status=Status.REJECTED, audit=AuditLog.Action.REJECT,
            metadata={"stage": stage})
    _announce("transfer_rejected", transfer)
    return transfer


@transaction.atomic
def cancel(transfer_id, actor, *, remarks=""):
    transfer = _lock(transfer_id)
    if not transfer.is_open:
        raise ValidationError({"status": (
            f"This transfer is {transfer.get_status_display().lower()} and cannot be "
            "cancelled.")})
    if actor.id != transfer.requested_by_id and not roles.is_admin(actor):
        raise PermissionDenied("Only the person who raised this transfer can cancel it.")
    stage = transfer.status
    transfer.status = Status.CANCELLED
    transfer.save(update_fields=["status", "updated_at"])
    _record(transfer, Action.CANCELLED, actor, remarks=remarks, from_status=stage,
            to_status=Status.CANCELLED)
    return transfer


@transaction.atomic
def add_attachment(transfer_id, actor, upload):
    transfer = _lock(transfer_id)
    if not transfer.is_open:
        raise ValidationError({"status": (
            "Attachments can only be added while the transfer is still in progress.")})
    attachment = AssetTransferAttachment.objects.create(
        transfer=transfer, file=upload, original_name=upload.name,
        size=getattr(upload, "size", 0) or 0, uploaded_by=actor,
        uploaded_by_name=_name(actor))
    _record(transfer, Action.ATTACHMENT_ADDED, actor,
            metadata={"file": upload.name, "size": attachment.size})
    return attachment


# --------------------------------------------------------------------------- #
# Read models
# --------------------------------------------------------------------------- #
def timeline(transfer):
    return [{
        "sequence": e.sequence, "action": e.action, "label": e.get_action_display(),
        "from_status": e.from_status, "to_status": e.to_status,
        "actor": e.actor_name or "System", "remarks": e.remarks,
        "metadata": e.metadata, "at": e.at,
    } for e in transfer.events.order_by("at", "sequence")]


def stage_tracker(transfer):
    """
    The named stages with a state each, for the status card.

    Derived from the record rather than stored, so it cannot drift from it.
    Submission is an event rather than a resting stage, and appears as the moment
    the draft was sent on.

    THE CHAIN IS BUILT PER RECORD, not from a fixed list (Phase
    ASSET-TRANSFER-GOVERNANCE). A transfer raised today has one gate; a transfer
    approved last month went through three, and its card must still show the
    department head and the administrator who signed it, by name. So a retired
    gate appears only when THIS record actually used it - it has a stamp, or the
    transfer is still resting there, or it was rejected there. Drawing today's
    short chain over an old record would erase two approvals from the history;
    drawing the old long chain over a new one would invent two gates nobody is
    waiting on.
    """
    used = []
    for stage in (Status.DEPT_HEAD_REVIEW, Status.ADMIN_APPROVAL):
        stamp = transfer.dept_head_at if stage == Status.DEPT_HEAD_REVIEW \
            else transfer.approved_at
        # `approved_at` is now stamped by HR on every completed transfer, so it
        # alone does not mean an administrator gate was passed; the event log does.
        touched = (transfer.status == stage or transfer.rejected_stage == stage
                   or (stage == Status.DEPT_HEAD_REVIEW and stamp is not None)
                   or (stage == Status.ADMIN_APPROVAL and transfer.events.filter(
                       action=Action.ADMIN_APPROVED).exists()))
        if touched:
            used.append(stage)

    order = ([Status.DRAFT]
             + [s for s in (Status.DEPT_HEAD_REVIEW,) if s in used]
             + [Status.HR_REVIEW]
             + [s for s in (Status.ADMIN_APPROVAL,) if s in used]
             + [Status.COMPLETED])
    stamps = {
        Status.DRAFT: (transfer.requested_by_name, transfer.created_at),
        Status.DEPT_HEAD_REVIEW: (transfer.dept_head_name, transfer.dept_head_at),
        Status.HR_REVIEW: (transfer.hr_name, transfer.hr_at),
        Status.ADMIN_APPROVAL: (transfer.approved_by_name, transfer.approved_at),
        Status.COMPLETED: ("", transfer.completed_at),
    }
    status = transfer.status
    if status == Status.REJECTED:
        pivot = order.index(transfer.rejected_stage) \
            if transfer.rejected_stage in order else None
    elif status == Status.CANCELLED:
        pivot = None
    else:
        pivot = order.index(status) if status in order else None

    stages = []
    for index, stage in enumerate(order):
        who, when = stamps[stage]
        if status == Status.COMPLETED:
            state = "done"
        elif pivot is not None and index < pivot:
            state = "done"
        elif pivot is not None and index == pivot:
            state = "rejected" if status == Status.REJECTED else "active"
        else:
            state = "pending"
        stages.append({"key": stage, "label": AssetTransfer.Status(stage).label,
                       "state": state, "by": who, "at": when})

    stages.insert(1, {"key": "submitted", "label": "Submitted",
                      "state": "done" if transfer.submitted_at else "pending",
                      "by": transfer.requested_by_name if transfer.submitted_at else "",
                      "at": transfer.submitted_at})
    return stages
