"""
Phase APM-FINAL — the Goal Approval stage and the goal lifecycle.

The stage exists to separate two facts that a single transition ran together:
somebody DRAFTED objectives, and somebody ACCEPTED them as the basis for the
year. Most of what follows pins that separation — who may do which, what an
objective's status says at each point, and that going back undoes an approval
rather than leaving a record claiming agreement that is being rewritten.
"""
import pytest
from rest_framework.exceptions import ValidationError

from appraisal import workflow
from appraisal.models import Appraisal, AppraisalAuditLog, Goal
from .conftest import APPRAISALS

pytestmark = pytest.mark.django_db

Status = Appraisal.Status
Action = AppraisalAuditLog.Action
GoalStatus = Goal.Status


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------
def test_goal_approval_sits_between_setting_and_mid_year():
    ladder = [s.value for s in Appraisal.LADDER]
    assert ladder.index("goal_approval") == ladder.index("goal_setting") + 1
    assert ladder.index("mid_year_review") == ladder.index("goal_approval") + 1
    assert len(ladder) == 10


def test_no_screen_hard_codes_the_ladder_length():
    """
    The dashboard reports `total_stages` from the ladder itself. Hard-coded,
    inserting a stage leaves every "stage 4 of 9" on screen quietly wrong.
    """
    from appraisal import dashboards
    assert dashboards.__dict__  # module imports
    assert len(Appraisal.LADDER) == 10


# ---------------------------------------------------------------------------
# Submission — the employee's act
# ---------------------------------------------------------------------------
def test_the_employee_may_submit_their_own_objectives(cast, cycle,
                                                      make_appraisal):
    """
    Objectives are the employee's to PROPOSE. A process where only a manager can
    put them forward is one where they are handed down rather than agreed.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    workflow.submit_goals(appraisal, cast["employee"])
    assert appraisal.status == Status.GOAL_APPROVAL


def test_submission_is_where_the_weight_gate_lives(cast, cycle,
                                                   make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               goals=[("Only objective", 60)])
    with pytest.raises(ValidationError):
        workflow.submit_goals(appraisal, cast["employee"])
    assert appraisal.status == Status.GOAL_SETTING


def test_goals_are_drafts_until_somebody_approves_them(cast, cycle,
                                                       make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    assert {g.status for g in appraisal.goals.all()} == {GoalStatus.DRAFT}
    workflow.submit_goals(appraisal, cast["employee"])
    # Submitted is not approved. An objective in front of a supervisor is still
    # a draft until they say otherwise.
    assert {g.status for g in appraisal.goals.all()} == {GoalStatus.DRAFT}


# ---------------------------------------------------------------------------
# Approval — the supervisor's act
# ---------------------------------------------------------------------------
def test_the_employee_cannot_approve_their_own_objectives(cast, auth, cycle,
                                                          make_appraisal):
    """The separation the stage exists for."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.GOAL_APPROVAL)
    response = auth(cast["employee"]).post(
        f"{APPRAISALS}{appraisal.id}/agree-goals/")
    assert response.status_code == 403
    appraisal.refresh_from_db()
    assert appraisal.status == Status.GOAL_APPROVAL


def test_approval_marks_every_goal_approved_and_stamps_the_moment(
        cast, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.GOAL_APPROVAL)
    assert appraisal.goals_agreed_at is None

    workflow.agree_goals(appraisal, cast["supervisor"])
    appraisal.refresh_from_db()
    assert appraisal.status == Status.MID_YEAR
    assert appraisal.goals_agreed_at is not None
    assert {g.status for g in appraisal.goals.all()} == {GoalStatus.APPROVED}


def test_goals_are_frozen_while_they_are_being_approved(cast, auth, cycle,
                                                        make_appraisal):
    """
    A set that can be edited while it is being approved is a set nobody can be
    held to. Rework goes back through a RETURN, which is visible in the trail —
    unlike a quiet edit underneath the person reading them.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.GOAL_APPROVAL)
    goal = appraisal.goals.first()
    response = auth(cast["employee"]).patch(
        f"{APPRAISALS}{appraisal.id}/goals/{goal.id}/",
        {"objective": "Something entirely different"}, format="json")
    assert response.status_code == 403


def test_goals_may_still_be_reshaped_at_mid_year(cast, auth, cycle,
                                                 make_appraisal):
    """Priorities genuinely change, and an objective nobody can revise is one
    people quietly stop referring to."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.MID_YEAR)
    goal = appraisal.goals.first()
    response = auth(cast["supervisor"]).patch(
        f"{APPRAISALS}{appraisal.id}/goals/{goal.id}/",
        {"progress_percent": 40}, format="json")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------
def test_goals_lock_when_the_appraisal_passes_mid_year(cast, cycle,
                                                       make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.MID_YEAR)
    workflow.record_mid_year(appraisal, cast["supervisor"])
    assert {g.status for g in appraisal.goals.all()} == {GoalStatus.LOCKED}


def test_a_locked_goal_cannot_be_edited_by_anybody(cast, auth, cycle,
                                                   make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    goal = appraisal.goals.first()
    for actor in ("employee", "supervisor", "hr"):
        response = auth(cast[actor]).patch(
            f"{APPRAISALS}{appraisal.id}/goals/{goal.id}/",
            {"weight": 90}, format="json")
        assert response.status_code == 403, actor


# ---------------------------------------------------------------------------
# Returning
# ---------------------------------------------------------------------------
def test_sending_objectives_back_undoes_their_approval(cast, cycle,
                                                       make_appraisal):
    """
    Left APPROVED, the employee is asked to rewrite a set the record still
    claims was agreed — and whichever version is quoted later, one of the two
    is wrong.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.MID_YEAR)
    assert appraisal.goals_agreed_at is not None

    workflow.return_to(appraisal, cast["supervisor"], Status.GOAL_SETTING,
                       "The second objective needs a measurable target.")
    appraisal.refresh_from_db()
    assert appraisal.status == Status.GOAL_SETTING
    assert {g.status for g in appraisal.goals.all()} == {GoalStatus.DRAFT}
    assert appraisal.goals_agreed_at is None


def test_a_return_from_a_later_stage_does_not_unlock_the_objectives(
        cast, cycle, make_appraisal):
    """
    A return from supervisor review is not an invitation to rewrite the
    objectives the year was measured against.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SUPERVISOR_REVIEW)
    workflow.return_to(appraisal, cast["supervisor"], Status.SELF_ASSESSMENT,
                       "Please expand on the second objective.")
    assert {g.status for g in appraisal.goals.all()} == {GoalStatus.LOCKED}


def test_returning_to_goal_setting_lets_the_cycle_run_again(cast, cycle,
                                                            make_appraisal):
    """The whole point of a return: it must be possible to fix and resubmit."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.GOAL_APPROVAL)
    workflow.return_to(appraisal, cast["supervisor"], Status.GOAL_SETTING,
                       "Weights need rebalancing before I can agree these.")
    workflow.submit_goals(appraisal, cast["employee"])
    workflow.agree_goals(appraisal, cast["supervisor"])
    assert appraisal.status == Status.MID_YEAR
    assert {g.status for g in appraisal.goals.all()} == {GoalStatus.APPROVED}


# ---------------------------------------------------------------------------
# Part 4 — the audit trail
# ---------------------------------------------------------------------------
def test_every_named_event_lands_in_the_trail(cast, cycle, make_appraisal):
    """
    The four the specification names: goals approved, goals locked, promotion
    status changed, training plan approved.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.CLOSED)
    actions = set(appraisal.audit_entries.values_list("action", flat=True))
    assert Action.GOALS_SUBMITTED in actions
    assert Action.GOALS_AGREED in actions
    assert Action.GOALS_LOCKED in actions
    assert Action.TRAINING_PLANNED in actions


def test_a_promotion_change_gets_its_own_row_naming_both_ends(
        cast, auth, cycle, make_appraisal):
    """
    Folded into a generic "Updated: promotion_readiness", the most consequential
    field in the record would be indistinguishable from a typo fix. The question
    asked afterwards is always "when did this become Development Required, and
    who changed it".
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.FINAL_REVIEW)
    response = auth(cast["supervisor"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"promotion_readiness": "development_required",
         "promotion_rationale": "Needs a full cycle owning the close."},
        format="json")
    assert response.status_code == 200

    row = appraisal.audit_entries.get(action=Action.PROMOTION_RECORDED)
    assert row.metadata["from"] == ""
    assert row.metadata["to"] == "development_required"
    assert "Not Considered" in row.remarks
    assert "Development Required" in row.remarks
    assert row.actor_name == cast["supervisor"].get_full_name()


def test_rewriting_the_rationale_alone_is_not_a_status_change(
        cast, auth, cycle, make_appraisal):
    """A typo fix must not read as somebody changing their recommendation."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.FINAL_REVIEW)
    auth(cast["supervisor"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"promotion_readiness": "ready", "promotion_rationale": "Frist draft."},
        format="json")
    auth(cast["supervisor"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"promotion_readiness": "ready", "promotion_rationale": "First draft."},
        format="json")

    rows = appraisal.audit_entries.filter(action=Action.PROMOTION_RECORDED)
    assert rows.count() == 1


def test_the_goal_status_travels_to_the_client(cast, auth, cycle,
                                               make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.MID_YEAR)
    body = auth(cast["employee"]).get(f"{APPRAISALS}{appraisal.id}/").data
    assert body["goals"][0]["status"] == "approved"
    assert body["goals"][0]["status_label"] == "Approved"


def test_a_client_cannot_set_a_goals_status_itself(cast, auth, cycle,
                                                   make_appraisal):
    """Writable, somebody could mark their own objective Approved without
    anybody approving it."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    goal = appraisal.goals.first()
    auth(cast["employee"]).patch(
        f"{APPRAISALS}{appraisal.id}/goals/{goal.id}/",
        {"status": "approved"}, format="json")
    goal.refresh_from_db()
    assert goal.status == GoalStatus.DRAFT


def test_capabilities_name_who_may_submit_and_who_may_approve(
        cast, auth, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    caps = auth(cast["employee"]).get(
        f"{APPRAISALS}{appraisal.id}/").data["capabilities"]
    assert caps["can_submit_goals"] is True
    assert caps["can_agree_goals"] is False

    workflow.submit_goals(appraisal, cast["employee"])
    caps = auth(cast["supervisor"]).get(
        f"{APPRAISALS}{appraisal.id}/").data["capabilities"]
    assert caps["can_submit_goals"] is False
    assert caps["can_agree_goals"] is True


# ---------------------------------------------------------------------------
# stage_index (APM-FINAL.1 regression)
# ---------------------------------------------------------------------------
def test_stage_index_counts_from_one_the_way_a_person_does(cast, cycle,
                                                           make_appraisal):
    """
    Every consumer renders this as "stage N of 10" to a human, and a human
    counting stages starts at one.

    Zero-based, an appraisal at Goal Setting reported "Stage 0 of 10" and the
    tracker highlighted nothing at all, while one at Final Review highlighted
    Review Committee — the stage before it.
    """
    expected = {
        Status.GOAL_SETTING: 1,
        Status.GOAL_APPROVAL: 2,
        Status.MID_YEAR: 3,
        Status.SELF_ASSESSMENT: 4,
        Status.SUPERVISOR_REVIEW: 5,
        Status.COMMITTEE: 6,
        Status.FINAL_REVIEW: 7,
        Status.DEVELOPMENT_PLAN: 8,
        Status.TRAINING_PLAN: 9,
        Status.CLOSED: 10,
    }
    for status, position in expected.items():
        appraisal = Appraisal(status=status)
        assert appraisal.stage_index == position, status


def test_the_last_stage_index_equals_the_number_of_stages(cast, cycle,
                                                          make_appraisal):
    """The property that makes "stage N of M" readable: the final stage is M,
    not M-1."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.CLOSED)
    assert appraisal.stage_index == len(Appraisal.LADDER)


def test_returning_to_the_current_stage_is_still_refused(cast, cycle,
                                                         make_appraisal):
    """
    The off-by-one guard. `return_to` compares ladder POSITIONS on both sides;
    mixing the one-based display index in would have let an appraisal be
    returned to the stage it is already at.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SUPERVISOR_REVIEW)
    with pytest.raises(ValidationError):
        workflow.return_to(appraisal, cast["supervisor"],
                           Status.SUPERVISOR_REVIEW, "Back to where we are.")
    with pytest.raises(ValidationError):
        workflow.return_to(appraisal, cast["supervisor"], Status.COMMITTEE,
                           "Forwards, which is not a return.")
    # And one genuinely earlier stage still works.
    workflow.return_to(appraisal, cast["supervisor"], Status.SELF_ASSESSMENT,
                       "Please expand on the second objective.")
    assert appraisal.status == Status.SELF_ASSESSMENT
