from django.db.models import Q
from rest_framework import permissions

from users.models import User
from .models import Memo, MemoWorkflowStep
from users.roles import has_org_wide_read as _org_wide_read

# A CONFIDENTIAL memo is never visible department-wide: "accessed only by the
# employees (by function) who have role (recommend / support / review / approve) in
# the memo" (E-memo-manual p.4). One axis now, not two - the old design also carried
# a `classification` column, and the manual has a single Memo Type dropdown.
RESTRICTED_TYPES = Memo.RESTRICTED_TYPES

# "A memo that has left the author's hands." Used only to bound what a department
# head may read - it does not grant org-wide access to anyone (see the note below).
PUBLIC_VISIBLE_STATUSES = {
    Memo.Status.DRAFT_FOR_REVIEW,
    Memo.Status.UNDER_REVIEW,
    Memo.Status.RECOMMENDED,
    Memo.Status.SUPPORTED,
    Memo.Status.APPROVED,
    Memo.Status.ARCHIVED,
    Memo.Status.REJECTED,
}

# ---------------------------------------------------------------------------
# Visibility model (Phase 10)
#
# Previously ANY submitted non-sensitive memo was readable by EVERY authenticated
# user in every department. That made "Memo Inbox" and "Department Memo" cosmetic
# filters over an org-wide pool rather than access boundaries, and it contradicted
# Phase 10's "Employee: view own memo". The rule is now:
#
#   Employee (maker)        -> own memos, plus any memo they appear in the
#                              approval matrix of (at any position, any status).
#   Department Head (checker)-> the above, plus every non-sensitive memo raised
#                              by their department.
#   HR (approver) / Admin   -> everything. HR owns the archive and the reports,
#                              so an org-wide read is inherent to the role.
#
# `visible_memo_filter()` and `CanViewMemo.has_object_permission()` implement the
# same rule twice, deliberately: the queryset keeps list endpoints cheap, the
# object check closes IDOR on detail endpoints even if an id leaks. They must be
# kept in lock-step - the test suite asserts they agree.
# ---------------------------------------------------------------------------


def _is_admin(user):
    return getattr(user, "role", None) == User.Roles.ADMIN


def _is_hr(user):
    return getattr(user, "role", None) == User.Roles.APPROVER


def has_org_wide_read(user):
    # HR, Admin and the Board (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE), read-only.
    return _is_admin(user) or _is_hr(user) or _org_wide_read(user)


def has_forensic_read(user):
    """Audit trails with client IPs and user agents: HR and Admin, not the Board."""
    return _is_admin(user) or _is_hr(user)


def _department_ids_headed_by(user):
    """Departments this user heads structurally (leaves.Department.head)."""
    return list(user.departments_headed.values_list("id", flat=True))


def visible_memo_filter(user):
    """
    Q object selecting the memos `user` may read. Returns None when the user has
    an org-wide read (caller should skip filtering entirely).
    """
    if has_org_wide_read(user):
        return None

    # Own memos, always, at any status.
    visible = Q(created_by=user)

    # Any memo this user is routed on, at any position and any step status - so
    # a supporter can open the memo before their turn arrives and read what the
    # reviewer said, which is what makes the queue usable.
    visible |= Q(workflow_steps__assignee=user)

    # Phase 49.5: anyone asked to NOTE the memo. Asking somebody to read a
    # document is a grant of the right to read it - without this the note round is
    # unusable, because the recipient gets a notification pointing at a memo that
    # 404s. Caught by test_noting_does_not_advance_the_workflow, which could not
    # even reach the note endpoint.
    #
    # Narrower than it looks: a note request is deliberate, per-memo, made by
    # someone already in the chain, and recorded with the requester's name in
    # MemoNoteAudit. It is the same shape of grant as being routed on the memo.
    visible |= Q(note_round__recipients__user=user)

    if getattr(user, "role", None) == User.Roles.CHECKER:
        dept_ids = set(_department_ids_headed_by(user))
        if getattr(user, "department_ref_id", None):
            dept_ids.add(user.department_ref_id)
        dept_scope = Q()
        if dept_ids:
            dept_scope |= Q(department_id__in=dept_ids)
        if user.department:
            # Legacy free-text department, matched on the memo's snapshot.
            dept_scope |= Q(department_name__iexact=user.department)
        if dept_scope:
            visible |= (
                dept_scope
                & Q(status__in=PUBLIC_VISIBLE_STATUSES)
                & ~Q(memo_type__in=RESTRICTED_TYPES)
            )

    # CC departments may read a GENERAL memo once it is archived: "In case of
    # general memo, employees in departments of cc list can also view the archived
    # memo" (E-memo-manual p.4). Narrow on purpose - GENERAL only, ARCHIVED only -
    # so copying a department in never widens access to a confidential memo or to
    # one still in flight.
    cc_scope = Q()
    if getattr(user, "department_ref_id", None):
        cc_scope |= Q(cc_departments__id=user.department_ref_id)
    if cc_scope:
        visible |= (
            cc_scope
            & Q(memo_type=Memo.MemoType.GENERAL)
            & Q(status=Memo.Status.ARCHIVED)
        )

    # Phase 49.5: an explicit grant on an ARCHIVED memo (blocker 4).
    #
    # Added last and OR-ed in, so it can only ever widen access - it cannot
    # weaken any rule above it. Deliberately NOT filtered by memo_type: a grant is
    # a considered disclosure decision made by the author, HR or an administrator
    # with a written reason attached, which is a stronger basis for access than the
    # department-wide default the type rules exist to restrain. Confidential memos
    # therefore CAN be shared - but only one memo at a time, only once archived,
    # and only with an auditable reason.
    from .governance import granted_memo_filter
    visible |= granted_memo_filter(user)

    return visible


class CanViewMemo(permissions.BasePermission):
    """
    Object-level read guard. Mirrors visible_memo_filter() exactly - see the
    module docstring for the rule it implements.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user

        if has_org_wide_read(user):
            return True

        if obj.created_by_id == user.id:
            return True

        # Routed on this memo at any position.
        if obj.workflow_steps.filter(assignee_id=user.id).exists():
            return True

        # Phase 49.5: asked to note it. The object-level twin of the
        # note_round clause in visible_memo_filter - see the note there.
        note_round = getattr(obj, "note_round", None)
        if note_round is not None and note_round.recipients.filter(
                user_id=user.id).exists():
            return True

        # CC department, archived GENERAL memo (p.4). Mirrors visible_memo_filter().
        if (
            obj.memo_type == Memo.MemoType.GENERAL
            and obj.status == Memo.Status.ARCHIVED
            and getattr(user, "department_ref_id", None)
            and obj.cc_departments.filter(id=user.department_ref_id).exists()
        ):
            return True

        # Department head reading their own department's non-restricted traffic.
        # Mirrors visible_memo_filter().
        if (
            getattr(user, "role", None) == User.Roles.CHECKER
            and not obj.is_restricted
            and obj.status in PUBLIC_VISIBLE_STATUSES
        ):
            if obj.department_id and obj.department_id in set(
                _department_ids_headed_by(user)
            ) | {getattr(user, "department_ref_id", None)}:
                return True
            if (
                user.department
                and obj.department_name
                and obj.department_name.lower() == user.department.lower()
            ):
                return True

        # Phase 49.5: an explicit archive grant. The object-level twin of
        # granted_memo_filter() - see the module docstring on why the rule is
        # implemented twice. Evaluated per request against live grants only, so a
        # revocation takes effect on the next read with nothing to invalidate.
        from .governance import has_grant
        if has_grant(user, obj):
            return True

        return False


class CanMutateMemo(permissions.BasePermission):
    """
    Write guard for update/partial_update on a memo.

    A memo is editable only by its author (or an admin) and only while it is a
    Draft or has been Rejected and is being revised. Archived and cancelled
    memos are immutable (Phase 10), as is any memo mid-workflow - editing the
    body under a reviewer who has already commented on it would invalidate the
    approvals given.
    """
    message = "This memo can no longer be edited."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if obj.is_read_only:
            self.message = (
                f"This memo is {obj.get_status_display().lower()} and is read-only."
            )
            return False
        if obj.status not in (Memo.Status.DRAFT, Memo.Status.REJECTED):
            self.message = (
                f"This memo is {obj.get_status_display().lower()} and is locked "
                "while it moves through its approval workflow."
            )
            return False
        if _is_admin(user):
            return True
        if obj.created_by_id != user.id:
            self.message = "Only the memo author can edit this memo."
            return False
        return True


class CanDeleteMemo(permissions.BasePermission):
    """
    Delete guard.

    Previously `destroy` was inherited from ModelViewSet with no object guard
    beyond CanViewMemo, which - because CanViewMemo granted every authenticated
    user read on any submitted non-sensitive memo - let any user hard-delete
    another user's memo. Deletion is now restricted to the author's own untouched
    draft, plus admins on anything not yet archived.
    """
    message = "This memo cannot be deleted."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if obj.status == Memo.Status.ARCHIVED:
            self.message = "Archived memos are permanent and cannot be deleted."
            return False
        if _is_admin(request.user):
            return True
        if obj.created_by_id != request.user.id:
            self.message = "Only the memo author can delete this memo."
            return False
        if obj.status != Memo.Status.DRAFT:
            self.message = (
                "Only a draft can be deleted. A memo that has entered the "
                "approval workflow is part of the audit record."
            )
            return False
        return True


class IsWorkflowParticipant(permissions.BasePermission):
    """
    Guard for the workflow action endpoint: the caller must hold a step in this
    memo's matrix (any position - the engine decides whether it is their turn and
    returns a precise message if it is not), or be an admin acting as override.
    """
    message = "You are not part of this memo's approval workflow."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if _is_admin(request.user):
            return True
        allowed = obj.workflow_steps.filter(
            assignee_id=request.user.id,
        ).exclude(status=MemoWorkflowStep.StepStatus.SKIPPED).exists()
        if not allowed and obj.is_read_only:
            # Same refusal, truer sentence (Phase MEMO-ACT-ENDPOINT-400-ROOT-CAUSE).
            # Withdrawing or archiving a memo skips its open steps, so a reviewer
            # who WAS on the chain a minute ago fails the check above - and was
            # told "You are not part of this memo's approval workflow", which is
            # false and sends them looking for a role assignment problem. Found in
            # the browser: the author withdrew the memo while the reviewer's
            # comment dialog was open. Whether they may act is unchanged; only
            # the reason given is corrected.
            self.message = (
                f"This memo is {obj.get_status_display().lower()} and can no longer "
                "be actioned.")
        return allowed
