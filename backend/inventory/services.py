"""Inventory business logic — race-safe sequences + atomic state transitions.

Every state change (assign / return / approve / reject / mark-returned) runs
inside transaction.atomic() and re-reads the row with select_for_update() so
concurrent actors serialize on the row lock (same pattern as memos/leaves).
"""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import InventoryItem, ItemAssignment, InventorySequence, TakeOutRequest


# --------------------------------------------------------------------------- #
# Reference / asset-code generation (gap-free, race-safe)
# --------------------------------------------------------------------------- #
@transaction.atomic
def _next_seq(key, year=0, organization=None):
    """Next value of one tenant's (key, year) counter.

    Phase S2 scoped the row to the organization. Before that, a single row per
    (key, year) served every tenant, so one company registering an asset
    advanced another company's asset codes.
    """
    from tenancy.scoping import active_organization

    organization = organization or active_organization()
    # Own transaction (or savepoint if already inside one) so select_for_update is
    # always valid — DRF's perform_create is not atomic by default.
    InventorySequence.objects.get_or_create(
        organization=organization, key=key, year=year)
    seq = (InventorySequence.objects.select_for_update()
           .get(organization=organization, key=key, year=year))
    seq.last_value += 1
    seq.save(update_fields=["last_value"])
    return seq.last_value


def current_bs_year():
    from config.nepali_dates import to_bs
    bs = to_bs(timezone.localtime(timezone.now()).date())
    return int(bs.split("-")[0]) if bs else timezone.now().year


def generate_asset_code():
    """NIF-INV-0001 for NIF; <PREFIX>-INV-0001 for any other tenant."""
    from tenancy import numbering
    from tenancy.scoping import active_organization

    organization = active_organization()
    return numbering.format_number(
        "INV", year=None, value=_next_seq("INV", 0, organization),
        organization=organization)


def generate_takeout_reference():
    """NIF-OUT-2083-0001 for NIF (scoped to the current BS year)."""
    from tenancy import numbering
    from tenancy.scoping import active_organization

    organization = active_organization()
    y = current_bs_year()
    return numbering.format_number(
        "OUT", year=y, value=_next_seq("OUT", y, organization),
        organization=organization)


def _name(user):
    if not user:
        return ""
    return user.get_full_name() or user.username


# --------------------------------------------------------------------------- #
# Direct assignment (who is using what)
# --------------------------------------------------------------------------- #
@transaction.atomic
def assign_item(item_id, assignee, actor, *, note="", assigned_date=None,
                handover_condition="", accessories="", is_handover=False,
                require_current_holder=False):
    item = InventoryItem.objects.select_for_update().get(pk=item_id)
    if item.status in (InventoryItem.Status.RETIRED, InventoryItem.Status.MAINTENANCE):
        raise ValidationError(f"Cannot assign a {item.get_status_display().lower()} item.")
    prior = ItemAssignment.objects.select_for_update().filter(item=item, is_active=True).first()
    if require_current_holder and prior is None:
        raise ValidationError("This item has no current holder to hand over from.")
    if prior and prior.assigned_to_id == getattr(assignee, "id", None):
        raise ValidationError("The item is already assigned to this employee.")
    # Close any prior active assignment first (keeps the one-active constraint).
    if prior:
        prior.is_active = False
        prior.returned_at = timezone.now()
        prior.save(update_fields=["is_active", "returned_at"])
    assignment = ItemAssignment.objects.create(
        item=item, item_code=item.asset_code, item_name=item.name,
        assigned_to=assignee, assigned_to_name=_name(assignee),
        assigned_by=actor, assigned_by_name=_name(actor), note=note,
        assigned_date=assigned_date or timezone.localtime(timezone.now()).date(),
        handover_condition=handover_condition or item.condition,
        accessories=accessories, is_handover=is_handover, is_active=True)
    item.status = InventoryItem.Status.ASSIGNED
    item.save(update_fields=["status", "updated_at"])
    return assignment


@transaction.atomic
def handover_item(item_id, new_assignee, actor, *, note="", assigned_date=None,
                  handover_condition="", accessories=""):
    """Transfer an item from its current holder to a new employee (staff change)."""
    return assign_item(
        item_id, new_assignee, actor, note=note, assigned_date=assigned_date,
        handover_condition=handover_condition, accessories=accessories,
        is_handover=True, require_current_holder=True)


@transaction.atomic
def return_item(item_id, actor, *, return_condition="", return_remarks=""):
    """End the current assignment: the holder gives the asset back to the office.

    This is the *assignment ended* path (distinct from ``mark_returned``, which only
    ends a take-out). Any APPROVED take-out for the item is also closed here — the
    asset is physically back, so leaving that request open would permanently block
    future take-outs.
    """
    item = InventoryItem.objects.select_for_update().get(pk=item_id)
    active = ItemAssignment.objects.select_for_update().filter(item=item, is_active=True).first()
    if active:
        active.is_active = False
        active.returned_at = timezone.now()
        active.return_condition = return_condition or ""
        active.return_remarks = return_remarks or ""
        active.save(update_fields=["is_active", "returned_at", "return_condition", "return_remarks"])
    _close_open_takeouts(item)
    if return_condition:
        item.condition = return_condition
    if item.status in (InventoryItem.Status.ASSIGNED, InventoryItem.Status.OUT):
        # The assignment above is now closed, so this settles to AVAILABLE unless
        # another active holder somehow remains.
        item.status = _settle_item_status(item)
    item.save(update_fields=["status", "condition", "updated_at"])
    return item


# --------------------------------------------------------------------------- #
# Take-out workflow
# --------------------------------------------------------------------------- #
def assert_item_state_takeable(item):
    """Raise if the item's OWN state forbids taking it out (missing / retired /
    under maintenance / already outside).

    This is the check that must hold at BOTH request time and approval time — the
    item can be retired or taken out by someone else in between. It deliberately
    excludes the in-flight-request check below: another *pending* request must not
    block this one's approval (two pendings would otherwise deadlock each other),
    while a competing *approved* one already shows up here as status OUT.
    """
    if item is None:
        raise ValidationError("Item is required.")
    # Phase 70.19-C. This was a denylist of three against a nine-value enum, so the
    # four statuses added when the lifecycle grew -- PROCUREMENT, RECEIVED, DISPOSED,
    # ARCHIVED -- were takeable by omission: an asset still on order, or one already
    # disposed of, passed this gate and could be approved. Now an ALLOWLIST, held in
    # takeout_eligibility so the selector and this check read the same rule, and so a
    # status added tomorrow is ineligible until somebody decides it is not.
    from .takeout_eligibility import ineligible_reason

    reason = ineligible_reason(item)
    if reason is not None:
        raise ValidationError(f"This item {reason} and cannot be taken out.")


def assert_item_takeable(item):
    """Request-time gate: the item's state must allow it AND no request may already
    be in flight (one active request per item)."""
    assert_item_state_takeable(item)
    existing = TakeOutRequest.objects.filter(
        item=item, status__in=[TakeOutRequest.Status.PENDING, TakeOutRequest.Status.APPROVED]
    ).exists()
    if existing:
        raise ValidationError("There is already an active take-out request for this item.")


def _close_open_takeouts(item):
    """Close APPROVED take-outs for an item that has physically come back.

    An APPROVED request means "the item is outside". Once the item is returned via
    any path, that request must reach a terminal state or ``assert_item_takeable``
    would block the item from ever being taken out again (it counts APPROVED rows
    as in-flight). PENDING requests are deliberately left alone: they are still
    legitimately awaiting a decision and blocking on them is intended behaviour.
    """
    qs = TakeOutRequest.objects.select_for_update().filter(
        item=item, status=TakeOutRequest.Status.APPROVED)
    closed = 0
    for req in qs:
        req.status = TakeOutRequest.Status.RETURNED
        req.actual_return_date = req.actual_return_date or timezone.localtime(timezone.now()).date()
        req.save(update_fields=["status", "actual_return_date", "updated_at"])
        closed += 1
    return closed


def _settle_item_status(item):
    """Resolve an item's status from ground truth after it comes back to the office:
    still directly assigned to someone -> ASSIGNED (holder retained), else AVAILABLE.
    Never downgrades a RETIRED / MAINTENANCE item."""
    if item.status in (InventoryItem.Status.RETIRED, InventoryItem.Status.MAINTENANCE):
        return item.status
    held = ItemAssignment.objects.filter(item=item, is_active=True).exists()
    return InventoryItem.Status.ASSIGNED if held else InventoryItem.Status.AVAILABLE


@transaction.atomic
def create_takeout(*, item, requester, purpose, reason, expected_out_date, expected_return_date):
    item = InventoryItem.objects.select_for_update().get(pk=item.pk)
    assert_item_takeable(item)
    # Nepal time: settings.TIME_ZONE is Asia/Kathmandu, so localtime() is NPT.
    today = timezone.localtime(timezone.now()).date()
    if expected_out_date < today:
        raise ValidationError("The take-out date cannot be in the past.")
    if expected_return_date < expected_out_date:
        raise ValidationError("Expected return date cannot be before the take-out date.")
    dept = getattr(requester, "department_ref", None)
    req = TakeOutRequest.objects.create(
        reference=generate_takeout_reference(),
        item=item, item_code=item.asset_code, item_name=item.name,
        requested_by=requester, requested_by_name=_name(requester), department=dept,
        purpose=purpose, reason=reason,
        expected_out_date=expected_out_date, expected_return_date=expected_return_date,
        status=TakeOutRequest.Status.PENDING,
    )
    return req


@transaction.atomic
def approve_takeout(req_id, actor, remarks=""):
    req = TakeOutRequest.objects.select_for_update().get(pk=req_id)
    if req.status != TakeOutRequest.Status.PENDING:
        raise ValidationError("Only a pending request can be approved.")
    # Re-validate against the item's CURRENT state: it may have been retired, sent
    # for maintenance, deleted or taken out by another request since this one was
    # raised.
    item = (InventoryItem.objects.select_for_update().get(pk=req.item_id)
            if req.item_id else None)
    assert_item_state_takeable(item)
    req.status = TakeOutRequest.Status.APPROVED
    req.approver = actor
    req.approver_name = _name(actor)
    req.approver_remarks = remarks
    req.action_date = timezone.now()
    req.save(update_fields=["status", "approver", "approver_name", "approver_remarks",
                            "action_date", "updated_at"])
    item.status = InventoryItem.Status.OUT
    item.save(update_fields=["status", "updated_at"])
    return req


@transaction.atomic
def reject_takeout(req_id, actor, remarks):
    remarks = (remarks or "").strip()
    if not remarks:
        raise ValidationError("A remark is required when rejecting a request.")
    req = TakeOutRequest.objects.select_for_update().get(pk=req_id)
    if req.status != TakeOutRequest.Status.PENDING:
        raise ValidationError("Only a pending request can be rejected.")
    req.status = TakeOutRequest.Status.REJECTED
    req.approver = actor
    req.approver_name = _name(actor)
    req.approver_remarks = remarks
    req.action_date = timezone.now()
    req.save(update_fields=["status", "approver", "approver_name", "approver_remarks",
                            "action_date", "updated_at"])
    return req


@transaction.atomic
def mark_returned(req_id, actor):
    req = TakeOutRequest.objects.select_for_update().get(pk=req_id)
    if req.status != TakeOutRequest.Status.APPROVED:
        raise ValidationError("Only an approved (currently out) item can be marked returned.")
    req.status = TakeOutRequest.Status.RETURNED
    req.actual_return_date = timezone.localtime(timezone.now()).date()
    req.save(update_fields=["status", "actual_return_date", "updated_at"])
    if req.item_id:
        item = InventoryItem.objects.select_for_update().get(pk=req.item_id)
        # The item is back in the office, but a take-out return does NOT end the
        # underlying assignment: if someone still holds it, it settles back to
        # ASSIGNED (holder retained) rather than falsely reading AVAILABLE.
        if item.status == InventoryItem.Status.OUT:
            item.status = _settle_item_status(item)
            item.save(update_fields=["status", "updated_at"])
    return req


# --------------------------------------------------------------------------- #
# Phase 70.3 - the ten named categories
# --------------------------------------------------------------------------- #
def seed_default_categories():
    """
    Ensure the ten categories the brief names exist.

    Idempotent and additive: `get_or_create` by name, and nothing is renamed or
    deleted. An organisation that has already invented its own categories keeps
    them - this adds the standard set alongside, it does not impose it.

    Called from a data migration and available as a management command, rather than
    hardcoded as model choices, because the module already had a category TABLE and
    an eleventh category should not need a deployment.
    """
    from .models import DEFAULT_CATEGORIES, InventoryCategory

    created = []
    for name, description in DEFAULT_CATEGORIES:
        _, was_created = InventoryCategory.objects.get_or_create(
            name=name, defaults={"description": description})
        if was_created:
            created.append(name)
    return created
