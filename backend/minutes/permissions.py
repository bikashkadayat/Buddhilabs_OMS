"""
Who may see and do what with a minute.

The rules live here and ONLY here. The API exposes them to the client as capability
flags on the detail payload (`can_edit`, `can_acknowledge`, ...) so the UI renders its
action bar from the server's answer instead of re-deriving authorization in
JavaScript, where it would drift.

ARCHIVE IS ENFORCED, NOT STYLED
-------------------------------
An archived minute is read-only because every mutating permission returns False for it
and the workflow engine refuses the transition - not because the buttons are hidden.
Hiding a button stops the honest user; the guard stops the request. There is a test
that PATCHes and DELETEs an archived minute directly and expects 403.

FOUR GROUNDS FOR VISIBILITY
---------------------------
You initiated it, you are its FRO, you are an involved user, or you are a participant.
The manual gives the FRO its own ground - "First reporting officer to access the
meeting minute" (p.4) is an access grant, not only a review routing.
"""
from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import permissions

from .models import Minute, MinuteParticipant
from users.roles import has_org_wide_read as _org_wide_read

User = get_user_model()

# Statuses a department head may see for minutes they are not routed on. A draft is
# nobody's business but its author's (and its FRO's) until it is submitted.
DEPARTMENT_VISIBLE_STATUSES = [
    Minute.Status.DRAFT_FOR_REVIEW,
    Minute.Status.PENDING_ACKNOWLEDGEMENT,
    Minute.Status.ARCHIVED,
]


def _is_admin(user):
    return (getattr(user, "role", None) == User.Roles.ADMIN
            or getattr(user, "is_superuser", False))


def _is_hr(user):
    """
    HR is the APPROVER role. The stored values are maker/checker/approver/admin and
    the business labels Employee/Department Head/HR/Admin sit on top of them, so
    `Roles.HR` does not exist - reaching for it raises AttributeError.
    """
    return getattr(user, "role", None) == User.Roles.APPROVER


def has_org_wide_read(user):
    """HR and Admin read everything; minutes are governance records."""
    # HR, Admin and the Board (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE), read-only.
    return _is_admin(user) or _is_hr(user) or _org_wide_read(user)


def has_forensic_read(user):
    """Audit trails with client IPs and user agents: HR and Admin, not the Board."""
    return _is_admin(user) or _is_hr(user)


def _department_ids_headed_by(user):
    from leaves.models import Department
    return Department.objects.filter(head=user).values_list("id", flat=True)


def visible_minute_filter(user):
    """
    Q object selecting the minutes `user` may read, or None for an org-wide reader
    (the caller then skips filtering entirely rather than building a tautology).
    """
    if has_org_wide_read(user):
        return None

    visible = Q(created_by=user)
    visible |= Q(fro=user)
    visible |= Q(involvements__user=user)
    visible |= Q(participants__user=user)

    if getattr(user, "role", None) == User.Roles.CHECKER:
        dept_ids = set(_department_ids_headed_by(user))
        if getattr(user, "department_ref_id", None):
            dept_ids.add(user.department_ref_id)
        dept_scope = Q()
        if dept_ids:
            dept_scope |= Q(department_id__in=dept_ids)
        if getattr(user, "department", ""):
            dept_scope |= Q(department_name__iexact=user.department)
        if dept_scope:
            visible |= dept_scope & Q(status__in=DEPARTMENT_VISIBLE_STATUSES)

    return visible


def can_read(user, minute):
    if has_org_wide_read(user):
        return True
    if minute.created_by_id == user.id:
        return True
    if minute.fro_id == user.id:
        return True
    if minute.involvements.filter(user=user).exists():
        return True
    if minute.participants.filter(user=user).exists():
        return True
    if (getattr(user, "role", None) == User.Roles.CHECKER
            and minute.status in DEPARTMENT_VISIBLE_STATUSES):
        dept_ids = set(_department_ids_headed_by(user))
        if getattr(user, "department_ref_id", None):
            dept_ids.add(user.department_ref_id)
        if minute.department_id and minute.department_id in dept_ids:
            return True
        if getattr(user, "department", "") and minute.department_name:
            return minute.department_name.lower() == user.department.lower()
    return False


# `can_view` is the name the serializer's export flag reads; one rule, two callers.
can_view = can_read


def can_edit(user, minute):
    """
    The initiator may edit their OWN minute at ANY stage (policy update,
    Sept 2026) - draft, under review, pending acknowledgement, even archived.

    The organisation asked for the person who raised a minute to be able to
    correct it without being blocked by status. Editing a minute participants
    have already acknowledged changes the record under them; that is the accepted
    trade-off of this policy, and every edit is still written to the audit trail.

    Everyone else is unchanged: the FRO may edit a minute that is with them for
    review, Admin may edit anything not archived, and nobody else may edit.
    """
    if minute.created_by_id == user.id:
        return True
    if minute.status in Minute.READ_ONLY_STATUSES:
        return False
    if (minute.fro_id == user.id
            and minute.status == Minute.Status.DRAFT_FOR_REVIEW):
        return True
    return _is_admin(user)


def can_manage_participants(user, minute):
    """
    The three member pickers are on the draft form only. Once the round is open the
    list is frozen: adding a present member would change a denominator people have
    already answered against.
    """
    if not minute.is_editable:
        return False
    return minute.created_by_id == user.id or _is_admin(user)


def can_send_for_review(user, minute):
    """Submit for Draft Review - the initiator, on a draft that has an FRO."""
    if minute.status != Minute.Status.DRAFT:
        return False
    if minute.fro_id is None:
        return False
    return minute.created_by_id == user.id or _is_admin(user)


def can_return_review(user, minute):
    """The FRO hands a draft back. Only while it is actually with them."""
    if minute.status != Minute.Status.DRAFT_FOR_REVIEW:
        return False
    return minute.fro_id == user.id or _is_admin(user)


def can_send_for_acknowledgement(user, minute):
    """
    Submit for Acknowledge. Available from a plain draft and from one that has been
    reviewed, because the manual puts both buttons on the draft together (p.7).
    Needs at least one member present - they are who is asked.
    """
    if minute.status not in (Minute.Status.DRAFT, Minute.Status.DRAFT_FOR_REVIEW):
        return False
    if not any(p.attendance == MinuteParticipant.Attendance.PRESENT
               for p in minute.participants.all()):
        return False
    return minute.created_by_id == user.id or _is_admin(user)


def can_acknowledge(user, minute):
    """
    A member recorded PRESENT, while the round is open and they have not answered.

    Independent of everything else: being the initiator or an admin does not let you
    acknowledge on someone else's behalf.
    """
    if minute.status != Minute.Status.PENDING_ACKNOWLEDGEMENT:
        return False
    row = next((p for p in minute.participants.all() if p.user_id == user.id), None)
    if row is None:
        return False
    return (row.must_acknowledge
            and row.ack_status != MinuteParticipant.AckStatus.ACKNOWLEDGED)


def can_remind_acknowledgements(user, minute):
    if minute.status != Minute.Status.PENDING_ACKNOWLEDGEMENT:
        return False
    if not any(p.must_acknowledge
               and p.ack_status == MinuteParticipant.AckStatus.PENDING
               for p in minute.participants.all()):
        return False
    return minute.created_by_id == user.id or _is_admin(user)


def can_delete(user, minute):
    """
    The minute's author (or an Admin) may delete it at ANY stage (policy update,
    Sept 2026) - including one that is pending acknowledgement or archived.

    Deletion is the author's authority alone: being the FRO, a participant, or HR
    does not confer it. This erases a governance record (and the acknowledgements
    on it), which is the accepted trade-off of the policy.
    """
    return minute.created_by_id == user.id or _is_admin(user)


def can_view_audit(user, minute):
    """The audit panel is for the initiator, the FRO, HR and Admin - not the Board."""
    return (has_forensic_read(user)
            or minute.created_by_id == user.id
            or minute.fro_id == user.id)


class CanViewMinute(permissions.BasePermission):
    message = "You do not have access to this minute."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        return can_read(request.user, obj)


class CanMutateMinute(permissions.BasePermission):
    """
    Guards PATCH/PUT. Separate from CanViewMinute because the viewset's read and write
    paths need different answers for the same object: a department head may read a
    colleague's minute and may not edit it.
    """
    message = "This minute can no longer be edited."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return can_read(request.user, obj)
        return can_edit(request.user, obj)


class CanDeleteMinute(permissions.BasePermission):
    message = "Only an unsent draft can be deleted."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return can_read(request.user, obj)
        return can_delete(request.user, obj)
