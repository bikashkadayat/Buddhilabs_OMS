"""
Asset disposal workflow (Phase ASSET-LIFECYCLE-DISPOSAL).

    DRAFT -> DEPT_HEAD_REVIEW -> ADMIN_APPROVAL -> DISPOSED
                  \\__________________\\_________-> REJECTED
    (any open state, by the requester) -----------> CANCELLED

WHAT THIS MODULE DOES NOT DO
----------------------------
It never deletes an asset and never writes its status. Final approval calls the
existing `lifecycle.dispose`, which keeps its own guards and is the only writer of
DISPOSED; a lost asset's custody is ended through `services.return_item`, still
the one place an assignment is closed. The disposal is the governance in front of
those changes, plus the figures that were true at the moment they happened.

Same design rules as transfers.py: one atomic block per transition under
select_for_update, a ValidationError/PermissionDenied a person can act on, an
append-only event plus a shared audit row for every change, notifications on commit.
"""
import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from audit.models import AuditLog
from audit.services import log_action
from users.models import User

from . import roles
from .depreciation import depreciation
from .lifecycle import MIN_REASON_LENGTH
from .models import (
    AssetDisposal, AssetDisposalAttachment, AssetDisposalEvent, AssetLifecycleEvent,
    AssetTransfer, InventoryItem, ItemAssignment, MaintenanceTicket,
)
from .services import _name, _next_seq

logger = logging.getLogger("inventory.disposals")

Status = AssetDisposal.Status
Action = AssetDisposalEvent.Action
Type = AssetDisposal.DisposalType


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def generate_disposal_number():
    """DSP-2026-0001 for NIF, Gregorian year, matching the transfer numbering."""
    from tenancy import numbering
    from tenancy.scoping import active_organization

    organization = active_organization()
    year = timezone.localdate().year
    return numbering.format_number(
        "DSP", year=year, value=_next_seq("DSP", year, organization),
        organization=organization)


def _announce(fn, *args):
    from . import notifications

    transaction.on_commit(lambda: getattr(notifications, fn)(*args))


def _lock(disposal_id):
    try:
        return AssetDisposal.objects.select_for_update().get(pk=disposal_id)
    except AssetDisposal.DoesNotExist:
        raise ValidationError({"disposal": "This disposal request does not exist."})


def _record(disposal, action, actor=None, *, remarks="", from_status="", to_status="",
            metadata=None, audit=AuditLog.Action.UPDATE):
    last = (AssetDisposalEvent.objects.filter(disposal=disposal)
            .order_by("-sequence").values_list("sequence", flat=True).first()) or 0
    AssetDisposalEvent.objects.create(
        disposal=disposal, sequence=last + 1, action=action,
        from_status=from_status or "", to_status=to_status or "",
        actor=actor, actor_name=_name(actor), remarks=remarks or "",
        metadata=metadata or {})
    log_action(actor, audit, instance=disposal, changes={
        "transition": action, "disposal_number": disposal.disposal_number,
        "from_status": from_status, "to_status": to_status,
        "remarks": remarks or "", **(metadata or {})})
    logger.info("disposal %s action=%s actor=%s", disposal.disposal_number, action,
                getattr(actor, "id", None))


def _require_text(value, field):
    value = (value or "").strip()
    if len(value) < MIN_REASON_LENGTH:
        raise ValidationError({field: (
            f"Give at least {MIN_REASON_LENGTH} characters. It is the only record of "
            "why an asset left the books.")})
    return value


def _active_holding(item):
    return ItemAssignment.objects.filter(item=item, is_active=True).first()


# --------------------------------------------------------------------------- #
# Authority
# --------------------------------------------------------------------------- #
def can_create(user):
    return roles.can_manage_assets(user)


def is_party(user, disposal):
    """The requester, and for a lost asset the person who lost it."""
    uid = getattr(user, "id", None)
    return uid is not None and uid in (disposal.requested_by_id, disposal.last_holder_id)


def heads_for(disposal):
    from .transfers import heads_for_department

    department_id = getattr(disposal.item, "department_id", None) if disposal.item_id else None
    return heads_for_department(department_id)


def can_act_at_stage(user, disposal, stage=None):
    """
    Department Head: the head of the department that OWNS the asset. Admin stands
    in there only when that department has no head - the same rule transfers use,
    so one administrator cannot clear both gates for a department that has a head.
    """
    stage = stage or disposal.status
    if not (user and user.is_authenticated) or is_party(user, disposal):
        return False
    if stage == Status.DEPT_HEAD_REVIEW:
        department_id = getattr(disposal.item, "department_id", None) if disposal.item_id else None
        if roles.is_department_head(user):
            return department_id is None or department_id == getattr(user, "department_ref_id", None)
        if roles.is_admin(user):
            return not heads_for(disposal).exists()
        return False
    if stage == Status.ADMIN_APPROVAL:
        return roles.is_admin(user)
    return False


def visible_to(user):
    """Store, HR and Admin see every disposal; a head sees their department's."""
    from django.db.models import Q

    qs = AssetDisposal.objects.all()
    if roles.is_admin(user) or roles.is_hr(user) or roles.is_inventory_officer(user):
        return qs
    if not roles.is_department_head(user):
        return qs.filter(Q(requested_by=user) | Q(last_holder=user))
    scope = (Q(requested_by=user) | Q(dept_head_by=user) | Q(rejected_by=user)
             # Visibility must cover authority (the lesson from transfers).
             | Q(status=Status.DEPT_HEAD_REVIEW, item__department__isnull=True))
    dept = getattr(user, "department_ref_id", None)
    if dept:
        scope |= Q(item__department_id=dept)
    return qs.filter(scope).distinct()


# --------------------------------------------------------------------------- #
# Guards shared by submission and completion
# --------------------------------------------------------------------------- #
def _check_disposable(item, disposal_type, *, excluding=None):
    if item is None:
        raise ValidationError({"item": "This asset no longer exists."})
    if item.is_terminal:
        raise ValidationError({"item": (
            f"{item.asset_code} is already {item.get_status_display().lower()}.")})
    if item.status in InventoryItem.PRE_STOCK_STATUSES:
        raise ValidationError({"item": (
            f"{item.asset_code} is still {item.get_status_display().lower()}. An asset "
            "that has not been checked in cannot be disposed of - cancel the order.")})
    if item.maintenance_tickets.filter(status__in=MaintenanceTicket.OPEN_STATUSES).exists():
        raise ValidationError({"item": (
            f"{item.asset_code} has an open maintenance ticket. Close it first.")})
    if AssetTransfer.objects.filter(item=item, status__in=AssetTransfer.OPEN_STATUSES).exists():
        raise ValidationError({"item": (
            f"{item.asset_code} has a transfer in progress. Finish or cancel it first.")})
    if disposal_type != Type.LOST and _active_holding(item) is not None:
        raise ValidationError({"item": (
            f"{item.asset_code} is still assigned to "
            f"{_active_holding(item).assigned_to_name}. Take it back before disposing "
            "of it - or, if it has been lost, choose \"Lost or stolen\".")})


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #
@transaction.atomic
def create_disposal(*, item, requested_by, disposal_type, reason, expected_proceeds=None,
                    remarks=""):
    if not can_create(requested_by):
        raise PermissionDenied("Only asset managers can request a disposal.")
    # of=("self",): lock the asset row ONLY. Joining the department (a nullable
    # FK, so a LEFT OUTER JOIN) inside FOR UPDATE is refused by PostgreSQL -
    # "FOR UPDATE cannot be applied to the nullable side of an outer join" - which
    # made every disposal request a 500. SQLite accepts it, so only a Postgres run
    # shows it.
    item = (InventoryItem.objects.select_related("department")
            .select_for_update(of=("self",)).get(pk=item.pk))
    reason = _require_text(reason, "reason")
    _check_disposable(item, disposal_type)
    if disposal_type == Type.SALE and expected_proceeds in (None, ""):
        raise ValidationError({"expected_proceeds": (
            "Say what the sale is expected to raise. It is what the write-off is "
            "measured against.")})
    if AssetDisposal.objects.filter(item=item, status__in=AssetDisposal.OPEN_STATUSES).exists():
        raise ValidationError({"item": (
            f"{item.asset_code} already has a disposal request in progress.")})

    holding = _active_holding(item)
    try:
        with transaction.atomic():
            disposal = AssetDisposal.objects.create(
                disposal_number=generate_disposal_number(), item=item,
                item_code=item.asset_code, item_name=item.name,
                department_name=getattr(item.department, "name", "") or "",
                disposal_type=disposal_type, reason=reason,
                expected_proceeds=(None if disposal_type in AssetDisposal.NO_PROCEEDS_TYPES
                                   else expected_proceeds),
                remarks=remarks or "",
                last_holder=holding.assigned_to if holding else None,
                last_holder_name=holding.assigned_to_name if holding else "",
                requested_by=requested_by, requested_by_name=_name(requested_by))
    except IntegrityError:
        raise ValidationError({"item": f"{item.asset_code} already has a disposal in progress."})

    _record(disposal, Action.CREATED, requested_by, remarks=reason, to_status=Status.DRAFT,
            audit=AuditLog.Action.CREATE,
            metadata={"asset": item.asset_code, "type": disposal_type,
                      "last_holder": disposal.last_holder_name})
    return disposal


@transaction.atomic
def submit(disposal_id, actor):
    disposal = _lock(disposal_id)
    if disposal.status != Status.DRAFT:
        raise ValidationError({"status": "Only a draft can be submitted."})
    if actor.id != disposal.requested_by_id and not roles.is_admin(actor):
        raise PermissionDenied("Only the person who raised this request can submit it.")
    _check_disposable(disposal.item, disposal.disposal_type)
    disposal.status = Status.DEPT_HEAD_REVIEW
    disposal.submitted_at = timezone.now()
    disposal.save(update_fields=["status", "submitted_at", "updated_at"])
    _record(disposal, Action.SUBMITTED, actor, from_status=Status.DRAFT,
            to_status=Status.DEPT_HEAD_REVIEW, audit=AuditLog.Action.SUBMIT)
    _announce("disposal_awaiting", disposal)
    return disposal


@transaction.atomic
def approve(disposal_id, actor, *, remarks=""):
    disposal = _lock(disposal_id)
    if disposal.status not in AssetDisposal.REVIEW_STAGES:
        raise ValidationError({"status": (
            f"This request is {disposal.get_status_display().lower()} and is not "
            "awaiting approval.")})
    if is_party(actor, disposal):
        raise PermissionDenied(
            "You raised this request or were holding the asset, so you cannot decide it.")
    if not can_act_at_stage(actor, disposal):
        raise PermissionDenied(
            f"This request is at {disposal.get_status_display()}, which you are not "
            "authorised to decide.")

    now = timezone.now()
    if disposal.status == Status.DEPT_HEAD_REVIEW:
        disposal.dept_head_by, disposal.dept_head_name = actor, _name(actor)
        disposal.dept_head_at, disposal.dept_head_remarks = now, remarks or ""
        disposal.status = Status.ADMIN_APPROVAL
        disposal.save()
        _record(disposal, Action.DEPT_HEAD_APPROVED, actor, remarks=remarks,
                from_status=Status.DEPT_HEAD_REVIEW, to_status=Status.ADMIN_APPROVAL,
                audit=AuditLog.Action.APPROVE)
        _announce("disposal_awaiting", disposal)
        return disposal

    # Final approval: approve and dispose in ONE transaction, so an approved
    # disposal can never describe an asset that is still on the books.
    disposal.approved_by, disposal.approved_by_name = actor, _name(actor)
    disposal.approved_at, disposal.admin_remarks = now, remarks or ""
    disposal.save()
    _record(disposal, Action.ADMIN_APPROVED, actor, remarks=remarks,
            from_status=Status.ADMIN_APPROVAL, to_status=Status.DISPOSED,
            audit=AuditLog.Action.APPROVE)
    return _complete(disposal, actor)


def _complete(disposal, actor):
    from .lifecycle import dispose, record_event
    from .services import return_item

    item = InventoryItem.objects.select_for_update().get(pk=disposal.item_id)
    _check_disposable(item, disposal.disposal_type)

    # Figures frozen BEFORE the status changes: this is what it was worth the day
    # it left the books.
    position = depreciation(item, as_of=timezone.localdate())
    proceeds = (Decimal("0.00") if disposal.disposal_type in AssetDisposal.NO_PROCEEDS_TYPES
                else Decimal(disposal.expected_proceeds or 0))

    holding = _active_holding(item)
    if disposal.disposal_type == Type.LOST and holding is not None:
        return_item(item.id, actor,
                    return_remarks=f"Reported lost under {disposal.disposal_number}")
        item.refresh_from_db()
        record_event(item, AssetLifecycleEvent.Event.LOST, actor, remarks=disposal.reason,
                     subject=holding.assigned_to_name,
                     metadata={"disposal_number": disposal.disposal_number})
        _record(disposal, Action.CUSTODY_CLOSED, actor,
                metadata={"holder": holding.assigned_to_name,
                          "assignment": str(holding.id)})

    dispose(item.id, actor, method=disposal.get_disposal_type_display(),
            reason=disposal.reason, value=proceeds)
    item.refresh_from_db()

    disposal.status = Status.DISPOSED
    disposal.disposed_at = item.disposed_at
    disposal.purchase_cost_at_disposal = position["purchase_cost"]
    disposal.accumulated_at_disposal = position["accumulated"]
    disposal.book_value_at_disposal = (position["book_value"] if position["depreciable"]
                                       else position["purchase_cost"])
    disposal.proceeds = proceeds
    disposal.save()
    _record(disposal, Action.DISPOSED, actor, from_status=Status.ADMIN_APPROVAL,
            to_status=Status.DISPOSED,
            metadata={"book_value": str(disposal.book_value_at_disposal),
                      "proceeds": str(proceeds),
                      "written_off": str(disposal.written_off),
                      "depreciable": position["depreciable"]})
    _announce("disposal_completed", disposal)
    return disposal


@transaction.atomic
def reject(disposal_id, actor, *, remarks):
    disposal = _lock(disposal_id)
    if disposal.status not in AssetDisposal.REVIEW_STAGES:
        raise ValidationError({"status": "This request is not awaiting a decision."})
    if is_party(actor, disposal):
        raise PermissionDenied(
            "You raised this request or were holding the asset, so you cannot decide it.")
    if not can_act_at_stage(actor, disposal):
        raise PermissionDenied(
            f"This request is at {disposal.get_status_display()}, which you are not "
            "authorised to decide.")
    remarks = _require_text(remarks, "remarks")
    stage = disposal.status
    disposal.status = Status.REJECTED
    disposal.rejected_by, disposal.rejected_by_name = actor, _name(actor)
    disposal.rejected_at, disposal.rejected_stage = timezone.now(), stage
    disposal.rejection_remarks = remarks
    disposal.save()
    _record(disposal, Action.REJECTED, actor, remarks=remarks, from_status=stage,
            to_status=Status.REJECTED, audit=AuditLog.Action.REJECT,
            metadata={"stage": stage})
    _announce("disposal_rejected", disposal)
    return disposal


@transaction.atomic
def cancel(disposal_id, actor, *, remarks=""):
    disposal = _lock(disposal_id)
    if not disposal.is_open:
        raise ValidationError({"status": "This request can no longer be cancelled."})
    if actor.id != disposal.requested_by_id and not roles.is_admin(actor):
        raise PermissionDenied("Only the person who raised this request can cancel it.")
    stage = disposal.status
    disposal.status = Status.CANCELLED
    disposal.save(update_fields=["status", "updated_at"])
    _record(disposal, Action.CANCELLED, actor, remarks=remarks, from_status=stage,
            to_status=Status.CANCELLED)
    return disposal


@transaction.atomic
def add_attachment(disposal_id, actor, upload):
    disposal = _lock(disposal_id)
    attachment = AssetDisposalAttachment.objects.create(
        disposal=disposal, file=upload, original_name=upload.name,
        size=getattr(upload, "size", 0) or 0, uploaded_by=actor,
        uploaded_by_name=_name(actor))
    _record(disposal, Action.ATTACHMENT_ADDED, actor,
            metadata={"file": upload.name, "size": attachment.size})
    return attachment


# --------------------------------------------------------------------------- #
# Read models
# --------------------------------------------------------------------------- #
def timeline(disposal):
    return [{
        "sequence": e.sequence, "action": e.action, "label": e.get_action_display(),
        "actor": e.actor_name or "System", "remarks": e.remarks,
        "metadata": e.metadata, "at": e.at,
    } for e in disposal.events.order_by("at", "sequence")]


def stage_tracker(disposal):
    order = [Status.DRAFT, Status.DEPT_HEAD_REVIEW, Status.ADMIN_APPROVAL, Status.DISPOSED]
    stamps = {
        Status.DRAFT: (disposal.requested_by_name, disposal.created_at),
        Status.DEPT_HEAD_REVIEW: (disposal.dept_head_name, disposal.dept_head_at),
        Status.ADMIN_APPROVAL: (disposal.approved_by_name, disposal.approved_at),
        Status.DISPOSED: ("", disposal.disposed_at),
    }
    status = disposal.status
    if status == Status.REJECTED:
        pivot = order.index(disposal.rejected_stage) if disposal.rejected_stage in order else None
    elif status == Status.CANCELLED:
        pivot = None
    else:
        pivot = order.index(status)
    stages = []
    for index, stage in enumerate(order):
        who, when = stamps[stage]
        if status == Status.DISPOSED or (pivot is not None and index < pivot):
            state = "done"
        elif pivot is not None and index == pivot:
            state = "rejected" if status == Status.REJECTED else "active"
        else:
            state = "pending"
        stages.append({"key": stage, "label": AssetDisposal.Status(stage).label,
                       "state": state, "by": who, "at": when})
    return stages
