from rest_framework.permissions import BasePermission, SAFE_METHODS

from users.models import User

# Managers who may add/edit items, assign/return, and approve/reject take-outs.
MANAGER_ROLES = (User.Roles.ADMIN, User.Roles.APPROVER, User.Roles.CHECKER)  # Admin / HR / Dept Head


def is_manager(user):
    """
    The original manager test: Admin, HR or Department Head.

    Phase 70 deliberately did NOT widen this. Three existing test files assert on
    its meaning, and quietly redefining a predicate that gates a dozen endpoints
    would be a security change disguised as a refactor. The new Inventory Officer
    role is admitted at the PERMISSION CLASSES below instead, where the widening is
    visible per endpoint and can be reasoned about one at a time.
    """
    return bool(user and user.is_authenticated and user.role in MANAGER_ROLES)


def _may_run_the_store(user):
    """
    Manager, or an inventory officer.

    Phase 70 added a role that runs the store without being senior - the person
    who issues assets may be a junior member of staff - and the pre-existing
    endpoints did not know about it. That was not a theoretical gap: the officer
    who approved a request, chose the asset and handed it over could not print the
    receipt for the handover they had just performed, because the receipt endpoint
    still asked the old question. Caught by generating the evidence PDFs rather
    than by reading the code.
    """
    from .roles import is_inventory_officer

    return is_manager(user) or is_inventory_officer(user)


class InventoryItemPermission(BasePermission):
    """
    Reading and writing the register are now two different questions.

    WRITE  - the store roles only (`can_manage_assets`).
    READ   - the store roles plus a Department Head, who sees the whole register
             across every department (`can_browse_register`).
    Anyone else - a single *retrieve*, and the viewset queryset then restricts even
             that to assets actively assigned to them, so a foreign id 404s.

    Before Phase ASSET-TRANSFER-GOVERNANCE one test answered both questions, so
    giving a head organisation-wide sight would have handed them organisation-wide
    edit rights at the same time. Splitting the test is what makes "read-only
    visibility" enforceable rather than a description of the UI.
    """
    def has_permission(self, request, view):
        from .roles import can_browse_register, can_manage_assets

        u = request.user
        if not (u and u.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            if can_browse_register(u):
                return True
            return getattr(view, "action", None) == "retrieve"
        return can_manage_assets(u)


class CanChangeCustody(BasePermission):
    """
    Assign, hand over, take back: the actions that move an asset between people.

    Separate from `IsManager` because a Department Head is still a manager for
    take-outs and the assignment board - neither of which changes who owns an
    asset - but must not move custody itself.
    """
    def has_permission(self, request, view):
        from .roles import can_manage_assets

        return can_manage_assets(request.user)


class IsManager(BasePermission):
    """
    Store-side endpoints: assign, return, approve, reject, mark_returned, receipts.

    The class keeps its name because it is referenced across `views.py` and the
    existing tests; what it ADMITS has widened to include inventory officers, which
    is the whole point of Phase 70's role work. `is_manager` itself is unchanged.
    """
    def has_permission(self, request, view):
        return _may_run_the_store(request.user)
