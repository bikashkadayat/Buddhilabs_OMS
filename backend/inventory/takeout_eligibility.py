"""
Who may take out WHICH asset (Phase 70.19).

THE PROBLEM THIS SOLVES
-----------------------
The take-out form used to build its dropdown from the generic asset register,
``GET /inventory/items/``. That endpoint answers a different question — "what is in
the register" — and `InventoryItemPermission` deliberately denies it to anyone who
does not run the store. So the form asked a question its own users are forbidden to
ask, got 403, and the client turned that into an empty list. The dropdown was blank
for every employee in the organisation, which is the population the feature exists
for.

Widening `/inventory/items/` would have been the cheap fix and the wrong one: that
endpoint gates the whole register, and loosening it to make a dropdown work would
hand every employee the full asset list, including assets belonging to colleagues.

So eligibility gets its own home. This module answers one question -- "which assets
may THIS user request a take-out for, and why" -- and both the list endpoint and the
create path consult it. That is what stops the two from disagreeing: before this,
the list hid an asset the create path would happily accept (an employee could take
out a colleague's laptop by pasting its id), and a rule enforced in one place but
not the other is not a rule.

THE TWO HALVES
--------------
``eligible_assets_for(user)``      -> the queryset the selector is built from.
``assert_may_request_takeout(...)`` -> the gate the create path must pass.

The second is defined in terms of the first, so they cannot drift apart: if you
cannot see it, you cannot request it.
"""
from django.core.exceptions import PermissionDenied

from users.models import User

from . import roles
from .models import InventoryItem, ItemAssignment

# ---------------------------------------------------------------------------
# Phase 70.19-C — status eligibility.
#
# Stated as an ALLOWLIST. The predecessor was a denylist of three
# ({RETIRED, MAINTENANCE, OUT}) against a nine-value enum, so the four states added
# later -- PROCUREMENT, RECEIVED, DISPOSED, ARCHIVED -- were take-out eligible by
# omission rather than by decision. An asset on order or already disposed of could
# be requested and approved. A denylist has to be revisited every time the lifecycle
# grows; an allowlist fails closed, and a new status is ineligible until somebody
# decides otherwise.
#
# AVAILABLE = in stock, nobody holds it. ASSIGNED = somebody holds it. Those are the
# only two states in which an asset is physically present and can leave the building.
# ---------------------------------------------------------------------------
TAKEOUT_ELIGIBLE_STATUSES = frozenset({
    InventoryItem.Status.AVAILABLE,
    InventoryItem.Status.ASSIGNED,
})

# Why each excluded status is excluded, surfaced verbatim in the 4xx body. A refusal
# that explains itself is the difference between a bug report and a user who knows
# what to do next.
_INELIGIBLE_REASON = {
    InventoryItem.Status.PROCUREMENT: "is still on order and has not been delivered",
    InventoryItem.Status.RECEIVED: "has been delivered but not yet checked into stock",
    InventoryItem.Status.OUT: "is already taken out",
    InventoryItem.Status.MAINTENANCE: "is under maintenance",
    InventoryItem.Status.RETIRED: "has been retired from service",
    InventoryItem.Status.DISPOSED: "has been disposed of",
    InventoryItem.Status.ARCHIVED: "has been archived",
}


def status_is_eligible(status):
    return status in TAKEOUT_ELIGIBLE_STATUSES


def ineligible_reason(item):
    """Human-readable why-not, or None when the status itself is fine."""
    if status_is_eligible(item.status):
        return None
    return _INELIGIBLE_REASON.get(
        item.status, f"is in a state that cannot be taken out ({item.status})")


# ---------------------------------------------------------------------------
# Phase 70.19-B — role scope.
# ---------------------------------------------------------------------------
# Scope keys are returned to the client alongside the assets so the UI can explain
# WHY a list looks the way it does ("your assigned assets") without re-deriving
# authorisation in JavaScript. Authorisation has one home, on the server.
SCOPE_OWN = "own_assigned"
SCOPE_OWN_PLUS_STOCK = "own_assigned_and_stock"
SCOPE_DEPARTMENT = "own_assigned_and_department"
SCOPE_ALL = "all_eligible"


def _active_assignment_filter(user):
    """
    Assets this user currently holds.

    NOTE on "accepted by employee" in the brief: `ItemAssignment` has no acceptance
    column -- `accepted_at` lives on `AssetRequest`, a different model covering the
    request-for-an-asset workflow. The fact that means "this person holds this
    asset" is `is_active=True`, which is set when the handover is recorded and
    cleared on return. That is the acceptance signal available today, so it is what
    is used; inventing an acceptance field would be a migration and a workflow
    change, not an eligibility fix. Flagged in the Phase 70.19 report.
    """
    return {"assignments__assigned_to": user, "assignments__is_active": True}


def scope_for(user):
    """Which of the four scopes this user gets. Ordered most senior first."""
    if roles.is_admin(user) or roles.is_hr(user):
        return SCOPE_ALL
    if roles.is_department_head(user):
        return SCOPE_DEPARTMENT
    if roles.is_inventory_officer(user):
        return SCOPE_OWN_PLUS_STOCK
    # Supervisor and Employee both get their own assets. A supervisor's seniority is
    # about approving OTHER people's requests (roles.can_approve_as_supervisor); it
    # does not entitle them to walk out with a colleague's laptop.
    return SCOPE_OWN


def eligible_assets_for(user):
    """
    The assets `user` may request a take-out for, as a queryset.

    Always status-filtered first, so no scope can widen its way past Phase 70.19-C.
    """
    from django.db.models import Q

    base = (InventoryItem.objects
            .select_related("category", "department")
            .filter(status__in=TAKEOUT_ELIGIBLE_STATUSES))
    scope = scope_for(user)

    if scope == SCOPE_ALL:
        qs = base
    elif scope == SCOPE_DEPARTMENT:
        # Own assets plus their department's. A Dept Head with no department falls
        # back to own-only: `department_id=None` would otherwise match every asset
        # with no department set, which is the same flaw the item list carries a
        # comment about.
        own = Q(**_active_assignment_filter(user))
        qs = base.filter(own) if user.department_ref_id is None else base.filter(
            own | Q(department_id=user.department_ref_id))
    elif scope == SCOPE_OWN_PLUS_STOCK:
        qs = base.filter(
            Q(**_active_assignment_filter(user))
            | Q(status=InventoryItem.Status.AVAILABLE))
    else:
        qs = base.filter(**_active_assignment_filter(user))

    # distinct(): the assignment join multiplies rows when an asset has a history of
    # them, and a selector that lists the same laptop three times looks broken.
    return qs.distinct().order_by("asset_code")


def _holds(user, item):
    return ItemAssignment.objects.filter(
        item=item, assigned_to=user, is_active=True).exists()


# ---------------------------------------------------------------------------
# Phase 70.19-D — ownership validation.
# ---------------------------------------------------------------------------
def assert_may_request_takeout(user, item):
    """
    Raise PermissionDenied unless `user` may raise a take-out for `item`.

    Deliberately defined as membership of `eligible_assets_for(user)` rather than as
    a parallel set of if-statements. Two independent implementations of one rule
    drift, and the drift is invisible until somebody exploits it -- which is exactly
    the defect this phase exists to close.

    Raises PermissionDenied (403), not ValidationError (400): "this asset is not
    yours" is an authorisation answer. The status-based refusals stay 400 via
    services.assert_item_takeable, because those are facts about the asset rather
    than about the caller.
    """
    if item is None:
        raise PermissionDenied("Item is required.")

    if eligible_assets_for(user).filter(pk=item.pk).exists():
        return

    # Distinguish the two ways a user can fail, so the message is actionable and the
    # audit trail records which control fired.
    reason = ineligible_reason(item)
    if reason is not None:
        raise PermissionDenied(f"This asset {reason} and cannot be taken out.")
    raise PermissionDenied(
        "This asset is not assigned to you. You may only request a take-out for "
        "assets you currently hold.")


def denial_kind(user, item):
    """
    Which control refused, for the audit log: 'status' | 'ownership' | None.

    Read-only and side-effect free -- the caller has already been refused; this only
    labels why.
    """
    if item is None:
        return "missing_item"
    if eligible_assets_for(user).filter(pk=item.pk).exists():
        return None
    return "status" if ineligible_reason(item) is not None else "ownership"
