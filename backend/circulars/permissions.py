"""
Who may see and do what with a circular (Phase 50).

THE VISIBILITY RULE IS THE OPPOSITE SHAPE TO A MEMO'S
-----------------------------------------------------
A memo is private by default and widens only to the people routed on it. A
circular, once broadcast, is meant to be READ - by its audience, which may be
everybody. So the rule is:

    Author                  -> always, at any status.
    Anyone in the chain     -> always (reviewer or issuer, before and after).
    A RECIPIENT             -> once broadcast. This is the clause that makes a
                               circular a circular.
    Department head         -> their department's circulars, once submitted,
                               unless restricted by classification.
    HR / Admin              -> everything.

A DRAFT IS NOBODY'S BUSINESS but its author's and its chain's, whatever its
classification - which is why the recipient clause is gated on BROADCASTED and
ARCHIVED rather than on the recipient row merely existing.

IMPLEMENTED TWICE, DELIBERATELY
-------------------------------
`visible_circular_filter()` keeps list endpoints cheap; `CanViewCircular` closes
IDOR on detail endpoints even if an id leaks. They must agree, and a test asserts
they do at every status - the same discipline the memo module holds itself to,
adopted here because that is where its value was proved.
"""
from django.db.models import Q
from rest_framework import permissions

from users.models import User

from .models import Circular
from users.roles import has_org_wide_read as _org_wide_read

# Statuses at which a circular has left its author and a department head may see
# it. A draft is excluded on purpose.
DEPARTMENT_VISIBLE_STATUSES = [
    Circular.Status.UNDER_REVIEW, Circular.Status.READY_FOR_ISSUE,
    Circular.Status.ISSUED, Circular.Status.READY_FOR_BROADCAST,
    Circular.Status.BROADCASTED, Circular.Status.ARCHIVED,
]

# Statuses at which being a recipient grants read. Before this the circular is not
# yet an announcement, so its audience has nothing to read.
BROADCAST_STATUSES = [Circular.Status.BROADCASTED, Circular.Status.ARCHIVED]


def _is_admin(user):
    return (getattr(user, "role", None) == User.Roles.ADMIN
            or getattr(user, "is_superuser", False))


def _is_hr(user):
    """HR is the APPROVER role; the stored values are maker/checker/approver/admin."""
    return getattr(user, "role", None) == User.Roles.APPROVER


def has_org_wide_read(user):
    # HR, Admin and the Board (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE), read-only.
    return _is_admin(user) or _is_hr(user) or _org_wide_read(user)


def has_forensic_read(user):
    """Audit trails with client IPs and user agents: HR and Admin, not the Board."""
    return _is_admin(user) or _is_hr(user)


def _department_ids(user):
    ids = set(user.departments_headed.values_list("id", flat=True))
    if getattr(user, "department_ref_id", None):
        ids.add(user.department_ref_id)
    return ids


def visible_circular_filter(user):
    """
    Q selecting the circulars `user` may read, or None for an org-wide reader (the
    caller then skips filtering rather than building a tautology).
    """
    if has_org_wide_read(user):
        return None

    visible = Q(created_by=user)
    visible |= Q(workflow_steps__assignee=user)
    # The recipient clause. Gated on status: being in the audience of a circular
    # that has not been broadcast yet grants nothing, because there is nothing to
    # have been told.
    visible |= (Q(recipients__user=user) & Q(status__in=BROADCAST_STATUSES))

    if getattr(user, "role", None) == User.Roles.CHECKER:
        ids = _department_ids(user)
        scope = Q()
        if ids:
            scope |= Q(department_id__in=ids)
        if getattr(user, "department", ""):
            scope |= Q(department_name__iexact=user.department)
        if scope:
            visible |= (
                scope
                & Q(status__in=DEPARTMENT_VISIBLE_STATUSES)
                # A restricted circular does not travel department-wide by default.
                # Its audience still reads it through the recipient clause above -
                # which is the point: restriction narrows the DEFAULT, it does not
                # override an explicit distribution.
                & ~Q(classification__in=Circular.RESTRICTED_CLASSIFICATIONS)
            )
    return visible


def can_read(user, circular):
    """Object-level twin of visible_circular_filter. Kept in lock-step by test."""
    if has_org_wide_read(user):
        return True
    if circular.created_by_id == user.id:
        return True
    if circular.workflow_steps.filter(assignee=user).exists():
        return True
    if (circular.status in BROADCAST_STATUSES
            and circular.recipients.filter(user=user).exists()):
        return True
    if getattr(user, "role", None) == User.Roles.CHECKER:
        if circular.status not in DEPARTMENT_VISIBLE_STATUSES:
            return False
        if circular.classification in Circular.RESTRICTED_CLASSIFICATIONS:
            return False
        ids = _department_ids(user)
        if circular.department_id and circular.department_id in ids:
            return True
        if getattr(user, "department", "") and circular.department_name:
            return circular.department_name.lower() == user.department.lower()
    return False


def can_edit(user, circular):
    """
    Content is editable by the author while the circular is a draft or was returned.

    An admin override exists for anything not yet OFFICIAL - and stops there. Once
    issued, the content is the text somebody signed; once broadcast, it is the text
    people were sent. Neither is an administrator's to rewrite. (This is the shape
    the minute module got wrong: its archive guard reads
    `and not _is_admin(actor)`, which hands an administrator a rewrite override on
    the permanent record. Phase 48 F-2, still open there.)
    """
    if circular.is_official or circular.is_read_only:
        return False
    if circular.created_by_id == user.id:
        return circular.is_editable
    return _is_admin(user) and circular.is_editable


def can_edit_chain(user, circular):
    from .workflow import CHAIN_EDITABLE_STATUSES
    if circular.status not in CHAIN_EDITABLE_STATUSES:
        return False
    return circular.created_by_id == user.id or _is_admin(user)


def can_submit(user, circular):
    from .workflow import CHAIN_EDITABLE_STATUSES
    if circular.status not in CHAIN_EDITABLE_STATUSES:
        return False
    if not circular.workflow_steps.exists():
        return False
    return circular.created_by_id == user.id or _is_admin(user)


def can_act(user, circular):
    """True when it is genuinely this user's turn, not merely that they are routed."""
    from .models import CircularWorkflowStep
    return circular.workflow_steps.filter(
        assignee=user,
        status=CircularWorkflowStep.StepStatus.ACTIVE).exists()


def can_cancel(user, circular):
    if circular.status == Circular.Status.BROADCASTED:
        return False
    if circular.status in Circular.TERMINAL_STATUSES:
        return False
    return (circular.created_by_id == user.id or _is_admin(user) or _is_hr(user))


def can_archive(user, circular):
    if circular.status != Circular.Status.BROADCASTED:
        return False
    return (circular.created_by_id == user.id or _is_admin(user) or _is_hr(user))


def can_delete(user, circular):
    """
    Only an unsubmitted draft, and only by its author or an admin.

    Anything that has entered the chain is part of the record and is cancelled, not
    deleted - and a broadcast circular can be neither.
    """
    if circular.status != Circular.Status.DRAFT:
        return False
    return circular.created_by_id == user.id or _is_admin(user)


def can_acknowledge(user, circular):
    """A recipient with an outstanding acknowledgement on a broadcast circular."""
    from .models import CircularAcknowledgement
    if not circular.acknowledgement_required:
        return False
    if circular.status not in BROADCAST_STATUSES:
        return False
    return CircularAcknowledgement.objects.filter(
        recipient__circular=circular, recipient__user=user,
        state=CircularAcknowledgement.State.PENDING).exists()


def can_view_registers(user, circular):
    """
    Who may see WHO ELSE received, read or acknowledged the circular.

    Deliberately narrower than reading it. A recipient is entitled to the
    announcement; they are not entitled to a list of which colleagues have not
    opened it yet, which is management information about other people.
    """
    return (has_org_wide_read(user)
            or circular.created_by_id == user.id
            or circular.issued_by_id == user.id
            or circular.workflow_steps.filter(assignee=user).exists())


def can_view_audit(user, circular):
    """The full trail with IP addresses: HR, Admin, and the author - not the Board."""
    return has_forensic_read(user) or circular.created_by_id == user.id


def can_export(user, circular):
    """PDF follows read - an archived circular's export is what the archive is for."""
    return can_read(user, circular)


# ---------------------------------------------------------------------------
# DRF permission classes
# ---------------------------------------------------------------------------
class CanViewCircular(permissions.BasePermission):
    message = "You do not have access to this circular."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        return can_read(request.user, obj)


class CanMutateCircular(permissions.BasePermission):
    """
    Guards PATCH/PUT. Separate from CanViewCircular because the read and write
    paths need different answers for the same object: a recipient may read a
    broadcast circular and may never edit one.
    """
    message = "This circular can no longer be edited."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return can_read(request.user, obj)
        return can_edit(request.user, obj)


class CanDeleteCircular(permissions.BasePermission):
    message = "Only an unsubmitted draft can be deleted."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return can_read(request.user, obj)
        return can_delete(request.user, obj)
