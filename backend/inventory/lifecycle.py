"""
The asset lifecycle engine (Phase 70.2, 70.5, 70.6, 70.8, 70.11).

WHY A SEPARATE MODULE
---------------------
`services.py` is the module as it was: direct assignment, direct return, and the
take-out workflow. Everything here is what the brief adds - the event log, the two
approval workflows, disposal, and maintenance - and it sits beside that file rather
than inside it so the pre-existing behaviour stays readable and diffable.

THE ONE RULE
------------
    Nothing changes an asset's status without writing an event.

That is enforced by `transition()` being the only function that assigns to
`item.status`, and every workflow below calling it. "Track every transition" is
then a property of the code rather than a discipline somebody has to remember, and
the asset history screen is a query rather than a reconstruction.

Design rules inherited from the modules that came before, because they were right:
every mutation is one `transaction.atomic` block; the row is re-read under
`select_for_update()` so two actors cannot both advance it; a guard failure raises
DRF `ValidationError`, which is a 400 with a message a person can act on.
"""
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import (
    AssetLifecycleEvent, AssetRequest, AssetReturn, InventoryItem, ItemAssignment,
    MaintenanceTicket,
)
from .services import _name, _next_seq, current_bs_year

logger = logging.getLogger("inventory")

Event = AssetLifecycleEvent.Event
Status = InventoryItem.Status

MIN_REASON_LENGTH = 10


def _announce(fn, *args):
    """
    Send a workflow notification once the transition has actually COMMITTED.

    Every mutation here runs inside `transaction.atomic`, so notifying inline would
    mean an email going out for a transition that a later guard rolled back - the
    in-app row would vanish with the rollback but the email would not, and the
    recipient would be told to act on a request that does not exist.
    `on_commit` makes the notification a consequence of the change rather than a
    bet on it.

    Imported inside the function because `notifications` imports the user model,
    and importing it at module scope would tie the lifecycle engine to app-loading
    order for no benefit.
    """
    from . import notifications

    transaction.on_commit(lambda: getattr(notifications, fn)(*args))


# --------------------------------------------------------------------------- #
# References
# --------------------------------------------------------------------------- #
def _reference(kind):
    """``<PREFIX>-<KIND>-<BS year>-<n>`` from this tenant's own counter."""
    from tenancy import numbering
    from tenancy.scoping import active_organization

    organization = active_organization()
    year = current_bs_year()
    return numbering.format_number(
        kind, year=year, value=_next_seq(kind, year, organization),
        organization=organization)


def generate_request_reference():
    """NIF-AR-2083-0001 for NIF, scoped to the BS year like the take-out ref."""
    return _reference("AR")


def generate_return_reference():
    return _reference("RT")


def generate_maintenance_reference():
    return _reference("MT")


# --------------------------------------------------------------------------- #
# The event log
# --------------------------------------------------------------------------- #
def record_event(item, event, actor=None, *, remarks="", subject="",
                 from_status="", to_status="", metadata=None):
    """
    Append one row to the asset's history. Never updates an existing one.

    Safe to call with `item=None` for an asset that has since been deleted - the
    snapshots carry the record, which is the whole reason they exist.
    """
    last = (AssetLifecycleEvent.objects.filter(item=item)
            .order_by("-sequence").values_list("sequence", flat=True).first()) or 0
    entry = AssetLifecycleEvent.objects.create(
        item=item,
        item_code=getattr(item, "asset_code", "") or "",
        item_name=getattr(item, "name", "") or "",
        sequence=last + 1, event=event,
        from_status=from_status or "", to_status=to_status or "",
        actor=actor, actor_name=_name(actor),
        subject_name=subject or "", remarks=remarks or "",
        metadata=metadata or {})
    logger.info("asset event=%s asset=%s actor=%s",
                event, getattr(item, "asset_code", None), getattr(actor, "id", None))
    return entry


def transition(item, to_status, event, actor=None, *, remarks="", subject="",
               metadata=None, save=True):
    """
    Move an asset to a new status AND record it. The only writer of `item.status`.

    A no-op status change still records the event when one is given - "inspected,
    condition unchanged" is a fact worth keeping - but does not write a pointless
    status update.
    """
    from_status = item.status
    if to_status and to_status != from_status:
        item.status = to_status
        if save:
            item.save(update_fields=["status", "updated_at"])
    record_event(item, event, actor, remarks=remarks, subject=subject,
                 from_status=from_status, to_status=item.status, metadata=metadata)
    return item


def history(item):
    """The asset's full timeline, oldest first (Phase 70.11)."""
    rows = (AssetLifecycleEvent.objects.filter(item=item)
            .select_related("actor").order_by("at", "sequence"))
    return [{
        "id": str(row.id),
        "sequence": row.sequence,
        "event": row.event,
        "label": row.get_event_display(),
        "from_status": row.from_status,
        "to_status": row.to_status,
        "actor": row.actor_name or "System",
        "subject": row.subject_name,
        "remarks": row.remarks,
        "metadata": row.metadata,
        "at": row.at,
    } for row in rows]


def _require_reason(value, field="reason"):
    value = (value or "").strip()
    if len(value) < MIN_REASON_LENGTH:
        raise ValidationError({field: (
            f"A reason of at least {MIN_REASON_LENGTH} characters is required. "
            "It is the only record of why this was done.")})
    return value


def _guard_actionable(item, what):
    """An asset that is disposed, archived or on order cannot be moved about."""
    if item is None:
        raise ValidationError({"item": "This asset no longer exists."})
    if item.is_terminal:
        raise ValidationError({"item": (
            f"{item.asset_code} is {item.get_status_display().lower()} and cannot "
            f"be {what}.")})
    if item.status in InventoryItem.PRE_STOCK_STATUSES:
        raise ValidationError({"item": (
            f"{item.asset_code} is {item.get_status_display().lower()} and has not "
            f"been checked into stock yet, so it cannot be {what}.")})


# ===========================================================================
# Procurement -> Received -> In Stock (Phase 70.2, the front of the lifecycle)
# ===========================================================================
@transaction.atomic
def mark_received(item_id, actor, *, remarks="", condition=""):
    """Delivery arrived. Not yet usable - it still has to be checked in."""
    item = InventoryItem.objects.select_for_update().get(pk=item_id)
    if item.status != Status.PROCUREMENT:
        raise ValidationError({"status": (
            "Only an asset on order can be marked received. "
            f"{item.asset_code} is {item.get_status_display().lower()}.")})
    if condition:
        item.condition = condition
        item.save(update_fields=["condition", "updated_at"])
    return transition(item, Status.RECEIVED, Event.RECEIVED, actor, remarks=remarks)


@transaction.atomic
def mark_stocked(item_id, actor, *, remarks="", location=""):
    """
    Checked in and on the shelf. This is the gate between "it arrived" and "it can
    be issued", and it exists because that window is where assets go missing.
    """
    item = InventoryItem.objects.select_for_update().get(pk=item_id)
    if item.status not in InventoryItem.PRE_STOCK_STATUSES:
        raise ValidationError({"status": (
            f"{item.asset_code} is already {item.get_status_display().lower()}.")})
    if location:
        item.location = location
        item.save(update_fields=["location", "updated_at"])
    return transition(item, Status.AVAILABLE, Event.STOCKED, actor, remarks=remarks,
                      metadata={"location": location} if location else None)


@transaction.atomic
def dispose(item_id, actor, *, method="", reason="", value=None):
    """
    Take an asset off the books for good.

    Refused while somebody still holds it: disposing of an assigned asset would
    leave a custody record pointing at something that no longer exists, and the
    holder would never be asked for it back.
    """
    item = InventoryItem.objects.select_for_update().get(pk=item_id)
    if item.is_terminal:
        raise ValidationError({"item": (
            f"{item.asset_code} is already {item.get_status_display().lower()}.")})
    reason = _require_reason(reason)
    if item.assignments.filter(is_active=True).exists():
        raise ValidationError({"item": (
            "This asset is still assigned. Take it back first - disposing of it "
            "now would leave a custody record pointing at nothing.")})
    if item.maintenance_tickets.filter(
            status__in=MaintenanceTicket.OPEN_STATUSES).exists():
        raise ValidationError({"item": (
            "This asset has an open maintenance ticket. Close it first.")})

    item.disposed_at = timezone.now()
    item.disposal_method = (method or "").strip()
    item.disposal_reason = reason
    item.disposal_value = value
    item.save(update_fields=["disposed_at", "disposal_method", "disposal_reason",
                             "disposal_value", "updated_at"])
    return transition(item, Status.DISPOSED, Event.DISPOSED, actor, remarks=reason,
                      metadata={"method": method, "value": str(value) if value else None})


@transaction.atomic
def archive_asset(item_id, actor, *, remarks=""):
    """
    File a disposed asset away. The record stays; it simply stops appearing in
    working lists.
    """
    item = InventoryItem.objects.select_for_update().get(pk=item_id)
    if item.status != Status.DISPOSED:
        raise ValidationError({"status": (
            "Only a disposed asset can be archived. Dispose of it first, so the "
            "record says how it left the organisation.")})
    return transition(item, Status.ARCHIVED, Event.ARCHIVED, actor, remarks=remarks)


# ===========================================================================
# Phase 70.5 - the assignment request workflow
# ===========================================================================
def _open_request_for(item):
    return AssetRequest.objects.filter(
        item=item, status__in=AssetRequest.HOLDING_STATUSES).first()


@transaction.atomic
def create_request(*, requester, purpose, item=None, category=None, needed_by=None,
                   supervisor=None):
    """
    An employee asks to be issued an asset.

    Either a specific asset or a category - "I need a laptop" is the common case,
    and making an employee pick an asset code whose availability they cannot see is
    how paper forms get filled in wrong.

    Deliberately does NOT reserve the asset. Two people may request the same laptop
    and the inventory officer's approval decides it; a request that reserved on
    submission would let anybody park an asset indefinitely.
    """
    purpose = _require_reason(purpose, "purpose")
    if item is None and category is None:
        raise ValidationError({"item": (
            "Name either a specific asset or a category.")})
    if item is not None:
        _guard_actionable(item, "requested")
        if item.assignments.filter(is_active=True).exists():
            raise ValidationError({"item": (
                f"{item.asset_code} is already assigned. Ask for a different asset "
                "or request the category instead.")})

    if AssetRequest.objects.filter(
            requested_by=requester, item=item,
            status__in=AssetRequest.OPEN_STATUSES).exists() and item is not None:
        raise ValidationError({"item": (
            "You already have an open request for this asset.")})

    request = AssetRequest.objects.create(
        reference=generate_request_reference(),
        item=item,
        item_code=getattr(item, "asset_code", "") or "",
        item_name=getattr(item, "name", "") or "",
        requested_category=category,
        requested_by=requester, requested_by_name=_name(requester),
        department=getattr(requester, "department_ref", None),
        purpose=purpose, needed_by=needed_by,
        supervisor=supervisor, supervisor_name=_name(supervisor))
    if item is not None:
        record_event(item, Event.REQUESTED, requester,
                     remarks=purpose, subject=_name(requester),
                     metadata={"reference": request.reference})
    _announce("asset_requested", request)
    return request


@transaction.atomic
def supervisor_decision(request_id, actor, *, approve, remarks=""):
    """
    First gate. The employee's supervisor confirms the business need.

    Not the same question the inventory officer answers: this one is "does this
    person need this", theirs is "do we have one and is it the right one". Two
    gates because they are two different judgements, and one person answering both
    is the control being skipped.
    """
    request = AssetRequest.objects.select_for_update().get(pk=request_id)
    if request.status != AssetRequest.Status.PENDING:
        raise ValidationError({"status": (
            f"This request is {request.get_status_display().lower()} and is past "
            "supervisor approval.")})
    if request.requested_by_id == getattr(actor, "id", None):
        raise PermissionDenied("You cannot approve your own asset request.")

    now = timezone.now()
    request.supervisor = actor
    request.supervisor_name = _name(actor)
    request.supervisor_remarks = (remarks or "").strip()
    request.supervisor_at = now
    if approve:
        request.status = AssetRequest.Status.SUPERVISOR_APPROVED
    else:
        request.status = AssetRequest.Status.REJECTED
        request.rejected_by_name = _name(actor)
        request.rejection_reason = _require_reason(remarks, "remarks")
    request.save()

    if request.item_id:
        record_event(
            request.item,
            Event.REQUEST_APPROVED if approve else Event.REQUEST_REJECTED,
            actor, remarks=request.supervisor_remarks,
            subject=request.requested_by_name,
            metadata={"reference": request.reference, "stage": "supervisor"})
    _announce("asset_request_at_inventory" if approve else "asset_request_refused",
              request)
    return request


@transaction.atomic
def inventory_decision(request_id, actor, *, approve, remarks="", item=None,
                       accessories=""):
    """
    Second gate. The inventory officer decides WHICH asset, and whether there is
    one.

    The officer may substitute the asset here - a request for a specific laptop
    that has since been assigned should not be refused when an identical one is on
    the shelf. This is the point at which the asset is actually held against the
    request.
    """
    request = AssetRequest.objects.select_for_update().get(pk=request_id)
    if request.status != AssetRequest.Status.SUPERVISOR_APPROVED:
        raise ValidationError({"status": (
            f"This request is {request.get_status_display().lower()}. It needs "
            "supervisor approval first.")})

    if not approve:
        request.status = AssetRequest.Status.REJECTED
        request.inventory_officer = actor
        request.inventory_officer_name = _name(actor)
        request.inventory_remarks = (remarks or "").strip()
        request.inventory_at = timezone.now()
        request.rejected_by_name = _name(actor)
        request.rejection_reason = _require_reason(remarks, "remarks")
        request.save()
        if request.item_id:
            record_event(request.item, Event.REQUEST_REJECTED, actor,
                         remarks=request.rejection_reason,
                         subject=request.requested_by_name,
                         metadata={"reference": request.reference,
                                   "stage": "inventory"})
        return request

    chosen = item or request.item
    if chosen is None:
        raise ValidationError({"item": (
            "Name the asset being issued. The request asked for a category, so "
            "somebody has to choose which one.")})
    chosen = InventoryItem.objects.select_for_update().get(pk=chosen.pk)
    _guard_actionable(chosen, "issued")
    if chosen.assignments.filter(is_active=True).exists():
        raise ValidationError({"item": (
            f"{chosen.asset_code} is already assigned to somebody.")})
    held = _open_request_for(chosen)
    if held is not None and held.pk != request.pk:
        raise ValidationError({"item": (
            f"{chosen.asset_code} is already held against request "
            f"{held.reference}.")})

    request.item = chosen
    request.item_code = chosen.asset_code
    request.item_name = chosen.name
    request.accessories = (accessories or "").strip()
    request.inventory_officer = actor
    request.inventory_officer_name = _name(actor)
    request.inventory_remarks = (remarks or "").strip()
    request.inventory_at = timezone.now()
    request.status = AssetRequest.Status.INVENTORY_APPROVED
    request.save()

    record_event(chosen, Event.REQUEST_APPROVED, actor,
                 remarks=request.inventory_remarks,
                 subject=request.requested_by_name,
                 metadata={"reference": request.reference, "stage": "inventory"})
    _announce("asset_request_ready", request)
    return request


@transaction.atomic
def hand_over(request_id, actor, *, condition="", accessories="", remarks=""):
    """
    The officer physically gives the asset to the employee.

    Creates the custody record. The asset is now ASSIGNED even though the employee
    has not confirmed yet - it has left the store, and pretending otherwise would
    let it be issued twice.
    """
    from .services import assign_item

    request = AssetRequest.objects.select_for_update().get(pk=request_id)
    if request.status != AssetRequest.Status.INVENTORY_APPROVED:
        raise ValidationError({"status": (
            f"This request is {request.get_status_display().lower()}; it is not "
            "ready for handover.")})
    if request.item_id is None:
        raise ValidationError({"item": "This request has no asset attached."})

    assignment = assign_item(
        request.item_id, request.requested_by, actor,
        note=f"Issued against {request.reference}",
        handover_condition=condition or request.item.condition,
        accessories=accessories or request.accessories)

    request.assignment = assignment
    request.handed_over_at = timezone.now()
    request.handover_condition = condition or request.item.condition
    if accessories:
        request.accessories = accessories
    request.status = AssetRequest.Status.HANDED_OVER
    request.save()

    item = InventoryItem.objects.get(pk=request.item_id)
    record_event(item, Event.HANDED_OVER, actor, remarks=remarks,
                 subject=request.requested_by_name,
                 from_status=item.status, to_status=item.status,
                 metadata={"reference": request.reference})
    _announce("asset_handed_over", request)
    return request


@transaction.atomic
def accept_asset(request_id, actor, *, remarks=""):
    """
    The employee confirms they have it.

    THE STEP THAT MAKES THE RECORD WORTH KEEPING. Without it, "assigned" means "an
    officer says they gave it to you" - and the first time an asset goes missing,
    that distinction is the entire argument. Only the requester can do this.
    """
    request = AssetRequest.objects.select_for_update().get(pk=request_id)
    if request.status != AssetRequest.Status.HANDED_OVER:
        raise ValidationError({"status": (
            f"This request is {request.get_status_display().lower()}; there is "
            "nothing to accept.")})
    if request.requested_by_id != getattr(actor, "id", None):
        raise PermissionDenied(
            "Only the person the asset was issued to can accept it. That is the "
            "point of the step.")

    request.status = AssetRequest.Status.ACCEPTED
    request.accepted_at = timezone.now()
    request.acceptance_remarks = (remarks or "").strip()
    request.save()

    if request.item_id:
        item = InventoryItem.objects.get(pk=request.item_id)
        record_event(item, Event.ACCEPTED, actor, remarks=request.acceptance_remarks,
                     subject=request.requested_by_name,
                     from_status=item.status, to_status=item.status,
                     metadata={"reference": request.reference})
    _announce("asset_accepted", request)
    return request


@transaction.atomic
def cancel_request(request_id, actor, *, remarks=""):
    """Withdraw a request that has not yet been handed over."""
    request = AssetRequest.objects.select_for_update().get(pk=request_id)
    if request.status in (AssetRequest.Status.HANDED_OVER,
                          AssetRequest.Status.ACCEPTED):
        raise ValidationError({"status": (
            "The asset has already been handed over. Return it instead - "
            "cancelling now would leave it in somebody's hands with no record.")})
    if request.status in (AssetRequest.Status.REJECTED,
                          AssetRequest.Status.CANCELLED):
        raise ValidationError({"status": "This request is already closed."})

    request.status = AssetRequest.Status.CANCELLED
    request.rejection_reason = (remarks or "").strip()
    request.rejected_by_name = _name(actor)
    request.save()
    if request.item_id:
        record_event(request.item, Event.REQUEST_REJECTED, actor,
                     remarks=request.rejection_reason,
                     subject=request.requested_by_name,
                     metadata={"reference": request.reference, "cancelled": True})
    return request


# ===========================================================================
# Phase 70.6 - the return workflow
# ===========================================================================
@transaction.atomic
def create_return(*, item, actor, reason="", declared_condition="",
                  declared_remarks=""):
    """
    The employee starts giving an asset back.

    Only the current holder may raise one, and only one at a time.
    """
    item = InventoryItem.objects.select_for_update().get(pk=item.pk)
    assignment = item.assignments.filter(is_active=True).first()
    if assignment is None:
        raise ValidationError({"item": (
            f"{item.asset_code} is not assigned to anybody.")})
    if assignment.assigned_to_id != getattr(actor, "id", None):
        raise PermissionDenied(
            "Only the person holding an asset can raise its return.")
    if AssetReturn.objects.filter(
            item=item, status__in=AssetReturn.OPEN_STATUSES).exists():
        raise ValidationError({"item": (
            "A return is already in progress for this asset.")})

    record = AssetReturn.objects.create(
        reference=generate_return_reference(),
        item=item, item_code=item.asset_code, item_name=item.name,
        assignment=assignment,
        returned_by=actor, returned_by_name=_name(actor),
        reason=(reason or "").strip(),
        declared_condition=declared_condition or "",
        declared_remarks=(declared_remarks or "").strip())
    record_event(item, Event.RETURN_REQUESTED, actor, remarks=record.reason,
                 subject=_name(actor),
                 metadata={"reference": record.reference,
                           "declared_condition": declared_condition})
    _announce("return_raised", record)
    return record


@transaction.atomic
def verify_return(return_id, actor, *, remarks=""):
    """The officer confirms the asset is physically in front of them."""
    record = AssetReturn.objects.select_for_update().get(pk=return_id)
    if record.status != AssetReturn.Status.REQUESTED:
        raise ValidationError({"status": (
            f"This return is {record.get_status_display().lower()}.")})
    record.status = AssetReturn.Status.VERIFYING
    record.verified_by = actor
    record.verified_by_name = _name(actor)
    record.verified_at = timezone.now()
    record.save()
    return record


@transaction.atomic
def inspect_return(return_id, actor, *, condition, remarks=""):
    """
    The officer records the condition they FIND.

    Separate from the employee's declaration on purpose. When the two differ,
    `condition_disputed` reports it - the system's job is to record that they did
    not agree, not to decide who was right.
    """
    record = AssetReturn.objects.select_for_update().get(pk=return_id)
    if record.status not in (AssetReturn.Status.REQUESTED,
                             AssetReturn.Status.VERIFYING):
        raise ValidationError({"status": (
            f"This return is {record.get_status_display().lower()}.")})
    if condition not in InventoryItem.Condition.values:
        raise ValidationError({"condition": (
            "Choose one of: " + ", ".join(InventoryItem.Condition.values))})
    if record.declared_condition and condition != record.declared_condition:
        # A disagreement has to be explained, or the register records a dispute
        # with no account of it.
        remarks = _require_reason(remarks, "remarks")

    record.status = AssetReturn.Status.INSPECTED
    record.inspected_condition = condition
    record.inspection_remarks = (remarks or "").strip()
    record.inspected_at = timezone.now()
    if record.verified_by_id is None:
        record.verified_by = actor
        record.verified_by_name = _name(actor)
        record.verified_at = timezone.now()
    record.save()
    return record


@transaction.atomic
def accept_return(return_id, actor, *, remarks=""):
    """
    Close the return: custody ends and the asset goes back into stock.

    Delegates the custody change to the pre-existing `return_item`, so there is
    still exactly one place that closes an assignment.
    """
    from .services import return_item

    record = AssetReturn.objects.select_for_update().get(pk=return_id)
    if record.status != AssetReturn.Status.INSPECTED:
        raise ValidationError({"status": (
            "Inspect the asset's condition before accepting the return - the "
            "condition it comes back in is the point of the inspection.")})
    if record.item_id is None:
        raise ValidationError({"item": "This asset no longer exists."})

    item = return_item(
        record.item_id, actor,
        return_condition=record.inspected_condition,
        return_remarks=record.inspection_remarks or record.declared_remarks)

    record.status = AssetReturn.Status.ACCEPTED
    record.accepted_at = timezone.now()
    record.returned_date = timezone.localdate()
    record.save()

    record_event(item, Event.RETURNED, actor,
                 remarks=remarks or record.inspection_remarks,
                 subject=record.returned_by_name,
                 to_status=item.status,
                 metadata={"reference": record.reference,
                           "condition": record.inspected_condition,
                           "disputed": record.condition_disputed})
    _announce("return_closed", record)
    return record


@transaction.atomic
def reject_return(return_id, actor, *, reason):
    """
    Refuse a return - the asset is not what was expected, or is not present.

    Custody does NOT end: the employee still holds it, which is the honest outcome.
    """
    record = AssetReturn.objects.select_for_update().get(pk=return_id)
    if record.status in (AssetReturn.Status.ACCEPTED, AssetReturn.Status.REJECTED):
        raise ValidationError({"status": "This return is already closed."})
    record.status = AssetReturn.Status.REJECTED
    record.rejection_reason = _require_reason(reason)
    record.verified_by = record.verified_by or actor
    record.verified_by_name = record.verified_by_name or _name(actor)
    record.save()
    if record.item_id:
        record_event(record.item, Event.RETURN_REQUESTED, actor,
                     remarks=f"Return refused: {record.rejection_reason}",
                     subject=record.returned_by_name,
                     metadata={"reference": record.reference, "rejected": True})
    _announce("return_closed", record)
    return record


# ===========================================================================
# Phase 70.8 - maintenance
# ===========================================================================
@transaction.atomic
def report_maintenance(*, item, actor, issue, priority="normal"):
    """
    Raise a ticket. Anybody may report a fault - including the person holding the
    asset, who is the most likely to notice one.

    The asset does not move to MAINTENANCE yet: reporting a fault is not the same
    as the asset going away to be fixed, and marking it unavailable on report would
    take a working laptop off somebody's desk the moment they mention a sticky key.
    """
    item = InventoryItem.objects.select_for_update().get(pk=item.pk)
    _guard_actionable(item, "sent for maintenance")
    issue = _require_reason(issue, "issue")
    if item.maintenance_tickets.filter(
            status__in=MaintenanceTicket.OPEN_STATUSES).exists():
        raise ValidationError({"item": (
            "This asset already has an open maintenance ticket.")})
    if priority not in MaintenanceTicket.Priority.values:
        raise ValidationError({"priority": (
            "Choose one of: " + ", ".join(MaintenanceTicket.Priority.values))})

    ticket = MaintenanceTicket.objects.create(
        reference=generate_maintenance_reference(),
        item=item, item_code=item.asset_code, item_name=item.name,
        reported_by=actor, reported_by_name=_name(actor),
        issue=issue, priority=priority,
        previous_item_status=item.status)
    record_event(item, Event.MAINTENANCE_REPORTED, actor, remarks=issue,
                 metadata={"reference": ticket.reference, "priority": priority})
    _announce("maintenance_reported", ticket)
    return ticket


@transaction.atomic
def assign_maintenance(ticket_id, actor, *, technician=None, vendor="", remarks=""):
    """Give the ticket to somebody - a colleague, or a named external repairer."""
    ticket = MaintenanceTicket.objects.select_for_update().get(pk=ticket_id)
    if ticket.status != MaintenanceTicket.Status.REPORTED:
        raise ValidationError({"status": (
            f"This ticket is {ticket.get_status_display().lower()}.")})
    if technician is None and not (vendor or "").strip():
        raise ValidationError({"technician": (
            "Name a technician or an external vendor.")})
    ticket.assigned_to = technician
    ticket.assigned_to_name = _name(technician)
    ticket.vendor = (vendor or "").strip()
    ticket.assigned_at = timezone.now()
    ticket.status = MaintenanceTicket.Status.ASSIGNED
    ticket.save()
    if ticket.item_id:
        record_event(ticket.item, Event.MAINTENANCE_REPORTED, actor, remarks=remarks,
                     subject=ticket.assigned_to_name or ticket.vendor,
                     metadata={"reference": ticket.reference, "stage": "assigned"})
    return ticket


@transaction.atomic
def start_maintenance(ticket_id, actor, *, remarks=""):
    """
    The asset physically goes away to be worked on.

    THIS is where the asset's status changes, not at report time - and any active
    assignment is closed, because the holder no longer has it. The status it held
    before is remembered so returning it to service puts it back rather than
    guessing.
    """
    from .services import return_item

    ticket = MaintenanceTicket.objects.select_for_update().get(pk=ticket_id)
    if ticket.status not in (MaintenanceTicket.Status.REPORTED,
                             MaintenanceTicket.Status.ASSIGNED):
        raise ValidationError({"status": (
            f"This ticket is {ticket.get_status_display().lower()}.")})
    if ticket.item_id is None:
        raise ValidationError({"item": "This asset no longer exists."})

    item = InventoryItem.objects.select_for_update().get(pk=ticket.item_id)
    ticket.previous_item_status = (
        Status.AVAILABLE if item.status in (Status.ASSIGNED, Status.OUT)
        else item.status)
    if item.assignments.filter(is_active=True).exists():
        # The holder does not have it any more. Closing the assignment here is what
        # stops an asset showing as "assigned to X" while it sits at a repair shop.
        return_item(item.id, actor,
                    return_remarks=f"Sent for maintenance ({ticket.reference})")
        item.refresh_from_db()

    ticket.status = MaintenanceTicket.Status.IN_MAINTENANCE
    ticket.started_at = timezone.now()
    ticket.save()
    transition(item, Status.MAINTENANCE, Event.MAINTENANCE_STARTED, actor,
               remarks=remarks, metadata={"reference": ticket.reference})
    return ticket


@transaction.atomic
def complete_maintenance(ticket_id, actor, *, resolution, condition="", cost=None):
    """Work finished. The asset is fixed but not yet back on the shelf."""
    ticket = MaintenanceTicket.objects.select_for_update().get(pk=ticket_id)
    if ticket.status != MaintenanceTicket.Status.IN_MAINTENANCE:
        raise ValidationError({"status": (
            f"This ticket is {ticket.get_status_display().lower()}; there is "
            "nothing to complete.")})
    ticket.resolution = _require_reason(resolution, "resolution")
    ticket.condition_after = condition or ""
    ticket.cost = cost
    ticket.completed_at = timezone.now()
    ticket.status = MaintenanceTicket.Status.COMPLETED
    ticket.save()
    if ticket.item_id:
        record_event(ticket.item, Event.MAINTENANCE_DONE, actor,
                     remarks=ticket.resolution,
                     metadata={"reference": ticket.reference,
                               "cost": str(cost) if cost is not None else None})
    _announce("maintenance_done", ticket)
    return ticket


@transaction.atomic
def return_to_service(ticket_id, actor, *, remarks=""):
    """Back on the shelf, in whatever status it held before it left."""
    ticket = MaintenanceTicket.objects.select_for_update().get(pk=ticket_id)
    if ticket.status != MaintenanceTicket.Status.COMPLETED:
        raise ValidationError({"status": (
            "Complete the work before returning the asset to service.")})
    if ticket.item_id is None:
        raise ValidationError({"item": "This asset no longer exists."})

    item = InventoryItem.objects.select_for_update().get(pk=ticket.item_id)
    if ticket.condition_after:
        item.condition = ticket.condition_after
        item.save(update_fields=["condition", "updated_at"])
    target = ticket.previous_item_status or Status.AVAILABLE
    if target not in (Status.AVAILABLE, Status.RETIRED):
        target = Status.AVAILABLE

    ticket.status = MaintenanceTicket.Status.RETURNED
    ticket.returned_at = timezone.now()
    ticket.save()
    transition(item, target, Event.MAINTENANCE_DONE, actor, remarks=remarks,
               metadata={"reference": ticket.reference, "returned_to_service": True})
    return ticket


@transaction.atomic
def cancel_maintenance(ticket_id, actor, *, reason):
    """
    Close a ticket raised in error. If the asset had already gone away, it comes
    back to stock - leaving it in MAINTENANCE with no open ticket would strand it.
    """
    ticket = MaintenanceTicket.objects.select_for_update().get(pk=ticket_id)
    if ticket.status in (MaintenanceTicket.Status.RETURNED,
                         MaintenanceTicket.Status.CANCELLED):
        raise ValidationError({"status": "This ticket is already closed."})
    reason = _require_reason(reason)
    was_away = ticket.status == MaintenanceTicket.Status.IN_MAINTENANCE
    ticket.status = MaintenanceTicket.Status.CANCELLED
    ticket.resolution = f"Cancelled: {reason}"
    ticket.returned_at = timezone.now()
    ticket.save()
    if ticket.item_id and was_away:
        item = InventoryItem.objects.select_for_update().get(pk=ticket.item_id)
        transition(item, ticket.previous_item_status or Status.AVAILABLE,
                   Event.MAINTENANCE_DONE, actor, remarks=reason,
                   metadata={"reference": ticket.reference, "cancelled": True})
    return ticket
