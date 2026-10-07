"""
Who may see and do what with an appraisal.

The rules live here and ONLY here, and the API exposes them as capability flags
so the UI renders its buttons from the server's answer.

THE FOUR SCOPES, AS SPECIFIED
-----------------------------
  Employee          their OWN appraisal
  Supervisor        their direct reports
  Review Committee  the appraisals they are assigned to
  HR / Admin        organisation-wide

WHY VISIBILITY HERE IS TIGHTER THAN ANYWHERE ELSE IN THIS SYSTEM
-----------------------------------------------------------------
Elsewhere a department head can see their department's work. Here they can see
their direct reports' APPRAISALS — which contain somebody's self-assessment,
their manager's private assessment of them, and a promotion recommendation. That
is categorically more sensitive than a task list, so membership is explicit
(`supervisor` on the row, or `committee` membership) rather than inferred from a
department. A department head is NOT automatically entitled to the appraisal of
everybody in their department; only to the people they actually supervise.

STAGE OWNERSHIP
---------------
Who may WRITE what changes with the stage, because an appraisal is a sequence of
turns. The employee writes their self-assessment while it is theirs to write and
not afterwards; the supervisor writes their review at their turn. This is not
about trust — it is about the record showing what each person said at the point
they said it, unedited by the next person in the chain.
"""
from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import permissions

from .models import Appraisal

User = get_user_model()
Status = Appraisal.Status


# ---------------------------------------------------------------------------
# Role predicates. The stored values are maker/checker/approver/admin with the
# business labels Employee/Department Head/HR/Admin on top, so `Roles.HR` does
# not exist — the same reason these helpers exist in the task module.
# ---------------------------------------------------------------------------
def is_admin(user):
    return (getattr(user, "role", None) == User.Roles.ADMIN
            or getattr(user, "is_superuser", False))


def is_hr(user):
    return getattr(user, "role", None) == User.Roles.APPROVER


def has_org_scope(user):
    """HR and Admin. Running the cycle is their job."""
    return is_admin(user) or is_hr(user)


def is_subject(user, appraisal):
    return appraisal.employee_id == user.id


def is_supervisor(user, appraisal):
    return appraisal.supervisor_id == user.id


def is_committee(user, appraisal):
    return appraisal.committee.filter(pk=user.pk).exists()


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------
def visible_appraisal_filter(user):
    """
    A Q selecting the appraisals `user` may read, or None for org-wide scope.

    Three grounds, all EXPLICIT: it is yours, you supervise it, or you are on its
    committee. Deliberately no department clause — see the module docstring.
    """
    # The Board reads every appraisal and writes none (Phase BOD-ROLE-EXECUTIVE-
    # GOVERNANCE). Granted HERE and in can_read only - `has_org_scope` also gates
    # running cycles and reassigning reviewers, which stay with HR and Admin.
    if has_org_scope(user) or getattr(user, "role", None) == User.Roles.BOD:
        return None
    return Q(employee=user) | Q(supervisor=user) | Q(committee=user)


def can_read(user, appraisal):
    if has_org_scope(user) or getattr(user, "role", None) == User.Roles.BOD:
        return True
    return (is_subject(user, appraisal) or is_supervisor(user, appraisal)
            or is_committee(user, appraisal))


can_view = can_read


# ---------------------------------------------------------------------------
# Administration
# ---------------------------------------------------------------------------
def can_manage_cycles(user):
    """Opening and closing appraisal rounds is HR's job."""
    return has_org_scope(user)


def can_create_appraisal(user):
    return has_org_scope(user)


def _live(appraisal):
    return not appraisal.is_closed


# ---------------------------------------------------------------------------
# Stage ownership — who writes what, when
# ---------------------------------------------------------------------------
def can_edit_goals(user, appraisal):
    """
    Goals are shaped by the employee and their supervisor, while the objectives
    are still being agreed or at the mid-year checkpoint. After that they are
    what the person was measured against, and editing them retrospectively
    changes the question they were asked.
    """
    if not _live(appraisal):
        return False
    if appraisal.status not in Appraisal.GOAL_EDITABLE_STATUSES:
        return False
    return (is_subject(user, appraisal) or is_supervisor(user, appraisal)
            or has_org_scope(user))


def can_submit_goals(user, appraisal):
    """
    Handing the drafted objectives to the supervisor.

    The EMPLOYEE may do this, as well as their supervisor and HR. Objectives are
    the employee's to propose, and a process where only a manager can put them
    forward is one where they are handed down rather than agreed.
    """
    return (_live(appraisal) and appraisal.status == Status.GOAL_SETTING
            and (is_subject(user, appraisal) or is_supervisor(user, appraisal)
                 or has_org_scope(user)))


def can_agree_goals(user, appraisal):
    """
    Approving the objective set. The supervisor or HR — never the employee.

    This is the separation the Goal Approval stage exists for: proposing your
    own objectives is yours, accepting them as the basis for the year is not.
    """
    return (_live(appraisal) and appraisal.status == Status.GOAL_APPROVAL
            and (is_supervisor(user, appraisal) or has_org_scope(user)))


def can_record_mid_year(user, appraisal):
    return (_live(appraisal) and appraisal.status == Status.MID_YEAR
            and (is_supervisor(user, appraisal) or has_org_scope(user)))


def can_write_self_assessment(user, appraisal):
    """
    The employee's own words, and ONLY the employee's. Not the supervisor, not
    HR, not an Admin — a self-assessment somebody else can write is not one.
    """
    return (_live(appraisal) and appraisal.status == Status.SELF_ASSESSMENT
            and is_subject(user, appraisal))


def can_submit_self_assessment(user, appraisal):
    return can_write_self_assessment(user, appraisal)


def can_write_supervisor_review(user, appraisal):
    return (_live(appraisal) and appraisal.status == Status.SUPERVISOR_REVIEW
            and (is_supervisor(user, appraisal) or has_org_scope(user)))


def can_write_committee_review(user, appraisal):
    return (_live(appraisal) and appraisal.status == Status.COMMITTEE
            and (is_committee(user, appraisal) or has_org_scope(user)))


def can_finalise(user, appraisal):
    """
    The final summary and any promotion recommendation. Supervisor or HR — never
    the subject: nobody signs off their own appraisal.
    """
    return (_live(appraisal) and appraisal.status == Status.FINAL_REVIEW
            and not is_subject(user, appraisal)
            and (is_supervisor(user, appraisal) or is_committee(user, appraisal)
                 or has_org_scope(user)))


def can_manage_development_plan(user, appraisal):
    """Agreed BETWEEN the employee and their supervisor, so both may write."""
    return (_live(appraisal)
            and appraisal.status in (Status.DEVELOPMENT_PLAN,
                                     Status.TRAINING_PLAN)
            and (is_subject(user, appraisal) or is_supervisor(user, appraisal)
                 or has_org_scope(user)))


def can_manage_training_plan(user, appraisal):
    return can_manage_development_plan(user, appraisal)


def can_decide_training(user):
    """
    Approving or declining training is HR's — it costs money and a calendar
    slot, which a supervisor cannot commit on the organisation's behalf.
    """
    return has_org_scope(user)


def can_rate(user, appraisal, role):
    """
    Competency ratings, by role.

    The employee rates themselves during self assessment; the supervisor at
    their review; the committee at theirs. Each writes into their own row, so
    the three views coexist and none overwrites another — the gap between a
    self-rating and a supervisor's is usually the most useful thing in the
    conversation.
    """
    if not _live(appraisal):
        return False
    if role == "employee":
        return (is_subject(user, appraisal)
                and appraisal.status == Status.SELF_ASSESSMENT)
    if role == "supervisor":
        return ((is_supervisor(user, appraisal) or has_org_scope(user))
                and appraisal.status == Status.SUPERVISOR_REVIEW)
    if role == "committee":
        return ((is_committee(user, appraisal) or has_org_scope(user))
                and appraisal.status == Status.COMMITTEE)
    return False


def can_attach_evidence(user, appraisal):
    """
    Anybody who can read a live appraisal may attach the evidence pack. It is a
    citation of figures they can already see, and making it a privilege would
    mean an employee could not put their own record in front of their reviewer.
    """
    return _live(appraisal) and can_read(user, appraisal)


def can_return(user, appraisal):
    """Sending it back: the supervisor, the committee or HR — not the subject."""
    return (_live(appraisal) and not is_subject(user, appraisal)
            and (is_supervisor(user, appraisal) or is_committee(user, appraisal)
                 or has_org_scope(user)))


def can_reopen(user, appraisal):
    """
    Reopening a CLOSED appraisal. HR and Admin only — it is the most sensitive
    action here, because it edits a record somebody may already have been given
    a copy of.
    """
    return appraisal.is_closed and has_org_scope(user)


def can_delete(user, appraisal):
    """
    Only an untouched appraisal, and only by HR — one raised against the wrong
    person, before anybody wrote anything. Once there is a self-assessment or a
    review in it, it is somebody's record and is corrected, not erased.
    """
    if not has_org_scope(user):
        return False
    if appraisal.status != Status.GOAL_SETTING:
        return False
    return not (appraisal.self_assessment or appraisal.supervisor_comments
                or appraisal.competency_ratings.exists())


def capabilities(user, appraisal):
    """Every flag the detail payload carries, computed in one place."""
    return {
        "can_view": can_read(user, appraisal),
        "can_edit_goals": can_edit_goals(user, appraisal),
        "can_submit_goals": can_submit_goals(user, appraisal),
        "can_agree_goals": can_agree_goals(user, appraisal),
        "can_record_mid_year": can_record_mid_year(user, appraisal),
        "can_write_self_assessment": can_write_self_assessment(user, appraisal),
        "can_submit_self_assessment": can_submit_self_assessment(user, appraisal),
        "can_write_supervisor_review": can_write_supervisor_review(user, appraisal),
        "can_write_committee_review": can_write_committee_review(user, appraisal),
        "can_finalise": can_finalise(user, appraisal),
        "can_manage_development_plan": can_manage_development_plan(user, appraisal),
        "can_manage_training_plan": can_manage_training_plan(user, appraisal),
        "can_decide_training": can_decide_training(user),
        "can_attach_evidence": can_attach_evidence(user, appraisal),
        "can_return": can_return(user, appraisal),
        "can_reopen": can_reopen(user, appraisal),
        "can_delete": can_delete(user, appraisal),
        "can_rate_as_employee": can_rate(user, appraisal, "employee"),
        "can_rate_as_supervisor": can_rate(user, appraisal, "supervisor"),
        "can_rate_as_committee": can_rate(user, appraisal, "committee"),
        "is_subject": is_subject(user, appraisal),
    }


# ---------------------------------------------------------------------------
# DRF classes
# ---------------------------------------------------------------------------
class CanViewAppraisal(permissions.BasePermission):
    message = "You do not have access to this appraisal."

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return can_read(request.user, obj)
        return True


class CanManageCycles(permissions.BasePermission):
    """
    Reading a cycle is open to any authenticated user; changing one is HR's.

    The asymmetry is deliberate and worth stating, because a reader auditing
    this class would otherwise have to guess whether the open read was a
    decision or an oversight. A cycle carries the round's name, its period and
    its three deadlines — precisely what somebody needs in order to know when
    their own self-assessment is expected. Gating that to HR would hide the
    deadlines from the people they apply to.

    Nothing personal lives on a cycle: no names, no assessments, no
    recommendations. The only non-calendar figure is `appraisal_count`, an
    organisation-wide total. The sensitivity is all in the WRITES — opening,
    activating and closing a round — and those are HR-only.
    """
    message = "Only HR or an Admin may manage appraisal cycles."

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return can_manage_cycles(request.user)
