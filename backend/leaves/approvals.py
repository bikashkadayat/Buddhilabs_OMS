"""Single source of truth for 'who must act' on a PENDING leave.

Both the notification layer (who is told "awaiting your review") and the pending
queue / count (what an approver can act on) derive from this module, so they can
never disagree.

Routing order — the employee's choice comes FIRST:
  1. The reporting manager the employee SELECTED on the application (Leave.approver)
     is the primary approver. Honoured whenever that person can still act
     (active + an approving role + not the applicant themselves).
  2. Fallback, when no manager was selected or the selected one can no longer act:
     the active Dept Head(s) of the applicant's department.
  3. Final fallback, when that department has no active Dept Head: HR.
  Admin is an oversight role and may act on any pending leave, but is never the
  *routing target* — a request is never silently handed to Admin alone.

Department is resolved by the structured `department_ref` when set, else by the
legacy free-text `department` string (backward compatible with old data).

BOARD ROUTING (Phase BOD-ROLE-EXECUTIVE-GOVERNANCE)
---------------------------------------------------
A Department Head's leave cannot be decided inside their own department - they
ARE its head - and used to fall through to HR. It now goes to the Board of
Directors. So does a Board member's own leave, which goes to the OTHER members.

    Employee          ->  Department Head  ->  Approved      (unchanged)
    Department Head   ->  Board            ->  Approved
    Board member      ->  other Board      ->  Approved

  * A Board member the applicant PICKED decides it; otherwise any active member.
  * No other active Board member -> HR steps in, for the same reason HR steps in
    for a department with no head: a request must never sit with nobody able to
    decide it.
  * The applicant's own reporting-manager choice of anyone who is NOT on the
    Board is not honoured for these applicants: the brief makes the Board the
    decider, and a head who picked a fellow head would otherwise route around it.
"""
from django.db.models import Exists, OuterRef, Q

from users.models import User
from .models import Leave

# Roles an employee may legitimately pick as their reporting manager. Mirrors the
# validation in views.LeaveViewSet.perform_create.
SELECTABLE_APPROVER_ROLES = (User.Roles.CHECKER, User.Roles.APPROVER, User.Roles.ADMIN)

# Applicants whose leave the Board decides, and the role that decides it.
BOARD_ROUTED_ROLES = (User.Roles.CHECKER, User.Roles.BOD)


def routes_to_board(applicant):
    """True when `applicant`'s leave is decided by the Board of Directors."""
    return getattr(applicant, "role", None) in BOARD_ROUTED_ROLES


def board_member_ids(applicant_id):
    """Active Board members who may decide this applicant's leave (never themselves)."""
    return set(User.objects.filter(role=User.Roles.BOD, is_active=True)
               .exclude(id=applicant_id).values_list("id", flat=True))


def _heads_for_applicant(applicant):
    """Active Dept Heads (checkers) for the applicant's department — by structured
    ref if present, else the legacy string. Empty if the department has no head."""
    heads = User.objects.filter(role=User.Roles.CHECKER, is_active=True).exclude(id=applicant.id)
    if applicant.department_ref_id:
        return heads.filter(department_ref_id=applicant.department_ref_id)
    if applicant.department:
        return heads.filter(department__iexact=applicant.department)
    return heads.none()


def selection_is_actionable(leave):
    """True when the manager the employee picked can actually act on this leave."""
    a = leave.approver
    return bool(
        a
        and a.is_active
        and a.role in SELECTABLE_APPROVER_ROLES
        and a.id != leave.user_id
    )


def _selection_ok_subquery():
    """Queryset mirror of selection_is_actionable().

    Expressed as EXISTS rather than a join + negated Q: a negated multi-table Q
    over a nullable FK is where Django/SQL three-valued logic silently drops rows.
    """
    return Exists(
        User.objects.filter(
            pk=OuterRef("approver_id"),
            is_active=True,
            role__in=SELECTABLE_APPROVER_ROLES,
        ).exclude(pk=OuterRef("user_id"))
    )


def _hr_ids(leave_user_id):
    return set(User.objects.filter(role=User.Roles.APPROVER, is_active=True)
               .exclude(id=leave_user_id).values_list("id", flat=True))


def actionable_approver_ids(leave):
    """User ids who must ACT on this PENDING leave — the exact set notified as
    'awaiting your review'. The employee's selected reporting manager wins; the
    department head / HR chain is only a fallback."""
    if leave.status != Leave.Status.PENDING or getattr(leave, "is_deleted", False):
        return set()
    # Department Heads and Board members: the Board decides (see module docstring).
    if routes_to_board(leave.user):
        board = board_member_ids(leave.user_id)
        if leave.approver_id in board:
            return {leave.approver_id}
        return board or _hr_ids(leave.user_id)
    # 1. The manager the employee chose is the primary approver.
    if selection_is_actionable(leave):
        return {leave.approver_id}
    # 2. No usable selection -> Dept Head(s) of the applicant's department.
    heads = _heads_for_applicant(leave.user)
    if heads.exists():
        return set(heads.values_list("id", flat=True))
    # 3. Department has no active Dept Head -> HR are the fallback grantors.
    return _hr_ids(leave.user_id)


def pending_actionable_leaves(user):
    """PENDING leaves `user` can act on — the queryset mirror of
    actionable_approver_ids (drives the pending list + count). A superset is
    acceptable (Admin oversight); the invariant is: anyone notified 'awaiting your
    review' for a leave finds that leave here."""
    base = (Leave.objects.filter(status=Leave.Status.PENDING, is_deleted=False)
            .exclude(user=user)
            .annotate(_selection_ok=_selection_ok_subquery()))
    role = user.role

    if role == User.Roles.ADMIN:
        return base  # oversight: may act on any pending leave

    board_routed = Q(user__role__in=BOARD_ROUTED_ROLES)
    if role == User.Roles.BOD:
        # Department Heads' and other Board members' leave - and nothing else:
        # employee leave is decided in the department.
        #
        # When the applicant ADDRESSED it to one Board member, only that member's
        # queue shows it. Found in the browser: the other member's queue listed
        # the request and the review endpoint then refused them - the queue and
        # the decision disagreeing is the one thing this module exists to prevent.
        addressed_to_board = User.objects.filter(
            pk=OuterRef("approver_id"), role=User.Roles.BOD, is_active=True,
        ).exclude(pk=OuterRef("user_id"))
        return (base.annotate(_addressed_to_board=Exists(addressed_to_board))
                .filter(board_routed)
                .filter(Q(_addressed_to_board=False) | Q(approver_id=user.id)))
    # Everyone below: never a Board-routed leave, except HR as the fallback.
    other_board = User.objects.filter(role=User.Roles.BOD, is_active=True).exclude(
        pk=OuterRef("user_id"))
    base = base.annotate(_board_can_decide=Exists(other_board))
    board_fallback = board_routed & Q(_board_can_decide=False)

    # Leaves this user was explicitly PICKED for (step 1) — applies to every
    # approving role, so a Dept Head chosen from another department still gets it.
    chosen = Q(approver_id=user.id) & Q(_selection_ok=True)
    # Fallback rules only apply when the selection cannot be honoured.
    unrouted = Q(_selection_ok=False)

    if role == User.Roles.CHECKER:
        # Dept Head: their own department's pending leaves (step 2).
        if user.department_ref_id:
            dept = Q(user__department_ref_id=user.department_ref_id)
        elif user.department:
            dept = Q(user__department__iexact=user.department)
        else:
            # No department -> no departmental claim at all. (Filtering on a None
            # department_ref would match every unscoped applicant.)
            return base.filter(chosen & ~board_routed)
        return base.filter((chosen | (unrouted & dept)) & ~board_routed)

    if role == User.Roles.APPROVER:
        # HR: leaves picked for them, plus (step 3) leaves whose department has NO
        # active Dept Head and which nobody was picked for.
        head_ref = User.objects.filter(
            role=User.Roles.CHECKER, is_active=True,
            department_ref_id=OuterRef("user__department_ref_id"))
        head_str = User.objects.filter(
            role=User.Roles.CHECKER, is_active=True,
            department__iexact=OuterRef("user__department"))
        base = base.annotate(_has_ref=Exists(head_ref), _has_str=Exists(head_str))
        covered = (Q(user__department_ref_id__isnull=False) & Q(_has_ref=True)) | \
                  (Q(user__department_ref_id__isnull=True) & Q(_has_str=True))
        return base.filter(((chosen | (unrouted & ~covered)) & ~board_routed) | board_fallback)

    return base.none()
