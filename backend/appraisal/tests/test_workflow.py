"""
The appraisal state machine — the specification's nine stages.

Every transition is exercised through the ENGINE, and every refusal is asserted
as a refusal rather than as an absence of change: a transition that silently
does nothing looks identical to one that is correctly blocked, right up until it
does something.
"""
import pytest
from rest_framework.exceptions import ValidationError

from appraisal import workflow
from appraisal.models import Appraisal, AppraisalAuditLog, DevelopmentPlan, Goal

pytestmark = pytest.mark.django_db

Status = Appraisal.Status


def test_the_ladder_is_exactly_the_ten_stages_specified():
    """Goal Approval sits between Goal Setting and Mid-Year Review (APM-FINAL)."""
    assert [s.value for s in Appraisal.LADDER] == [
        "goal_setting", "goal_approval", "mid_year_review", "self_assessment",
        "supervisor_review", "review_committee", "final_review",
        "development_plan", "training_plan", "closed"]


def test_a_full_appraisal_walks_every_stage(cast, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               [cast["committee"]], status=Status.CLOSED)
    assert appraisal.status == Status.CLOSED

    actions = list(appraisal.audit_entries.values_list("action", flat=True))
    for expected in ("created", "goals_agreed", "mid_year_recorded",
                     "self_assessed", "supervisor_reviewed",
                     "committee_reviewed", "finalised", "development_planned",
                     "training_planned"):
        assert expected in actions, expected


def test_every_stage_stamps_its_own_time(cast, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               [cast["committee"]], status=Status.CLOSED)
    for stamp in ("goals_agreed_at", "mid_year_at", "self_assessed_at",
                  "supervisor_reviewed_at", "committee_reviewed_at",
                  "finalised_at", "closed_at"):
        assert getattr(appraisal, stamp) is not None, stamp


def test_the_timeline_records_both_ends_of_a_transition(cast, cycle,
                                                        make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.MID_YEAR)
    row = appraisal.audit_entries.get(
        action=AppraisalAuditLog.Action.GOALS_AGREED)
    # Approval now runs from Goal Approval, not from Goal Setting: submission
    # and acceptance are separate stages, and separate rows (APM-FINAL).
    assert row.from_status == Status.GOAL_APPROVAL
    assert row.to_status == Status.MID_YEAR
    submitted = appraisal.audit_entries.get(
        action=AppraisalAuditLog.Action.GOALS_SUBMITTED)
    assert submitted.from_status == Status.GOAL_SETTING
    assert submitted.to_status == Status.GOAL_APPROVAL


# ---------------------------------------------------------------------------
# The one arithmetic gate
# ---------------------------------------------------------------------------
def test_goals_must_total_one_hundred_percent(cast, cycle, make_appraisal):
    """
    A completeness check on the plan, not a judgement of the person: goals
    weighted to 80% mean somebody has not finished writing them, and finding
    that out at final review is too late.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               goals=[("Only objective", 60)])
    # The gate is on SUBMISSION, so the person told about an incomplete set is
    # the one who can still fix it, at the moment they try to hand it over.
    with pytest.raises(ValidationError) as exc:
        workflow.submit_goals(appraisal, cast["employee"])
    assert "100" in str(exc.value)
    appraisal.refresh_from_db()
    assert appraisal.status == Status.GOAL_SETTING


def test_goals_totalling_more_than_one_hundred_are_refused(cast, cycle,
                                                           make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               goals=[("A", 70), ("B", 70)])
    with pytest.raises(ValidationError):
        workflow.agree_goals(appraisal, cast["supervisor"])


def test_an_appraisal_with_no_goals_cannot_be_agreed(cast, cycle,
                                                     make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    appraisal.goals.all().delete()
    with pytest.raises(ValidationError):
        workflow.agree_goals(appraisal, cast["supervisor"])


# ---------------------------------------------------------------------------
# Each stage needs its own work done
# ---------------------------------------------------------------------------
def test_an_empty_self_assessment_cannot_be_submitted(cast, cycle,
                                                      make_appraisal):
    """
    An appraisal that proceeds with a blank self-assessment is one where the
    person's own view was never on the record, and the rest happens about them
    rather than with them.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    with pytest.raises(ValidationError):
        workflow.submit_self_assessment(appraisal, cast["employee"])


def test_an_empty_supervisor_review_cannot_be_submitted(cast, cycle,
                                                        make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SUPERVISOR_REVIEW)
    with pytest.raises(ValidationError):
        workflow.record_supervisor_review(appraisal, cast["supervisor"])


def test_an_empty_final_summary_cannot_be_recorded(cast, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.FINAL_REVIEW)
    with pytest.raises(ValidationError):
        workflow.record_final_review(appraisal, cast["supervisor"])


def test_a_promotion_recommendation_needs_its_rationale(cast, cycle,
                                                        make_appraisal):
    """A recommendation with no reasoning cannot be defended later, in any of
    the three directions."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.FINAL_REVIEW)
    appraisal.final_summary = "A good year overall."
    appraisal.promotion_readiness = Appraisal.PromotionReadiness.READY
    appraisal.save(update_fields=["final_summary", "promotion_readiness"])

    with pytest.raises(ValidationError):
        workflow.record_final_review(appraisal, cast["supervisor"])

    appraisal.promotion_rationale = "Consistently operating a grade above."
    appraisal.save(update_fields=["promotion_rationale"])
    workflow.record_final_review(appraisal, cast["supervisor"])
    assert appraisal.status == Status.DEVELOPMENT_PLAN


def test_a_development_plan_needs_at_least_one_action(cast, cycle,
                                                      make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.DEVELOPMENT_PLAN)
    with pytest.raises(ValidationError):
        workflow.agree_development_plan(appraisal, cast["supervisor"])


def test_a_training_plan_may_legitimately_be_empty(cast, cycle, make_appraisal):
    """
    Not every appraisal identifies a course. Forcing a row would produce
    invented ones, which is worse than none.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.TRAINING_PLAN)
    workflow.agree_training_plan(appraisal, cast["supervisor"])
    assert appraisal.status == Status.CLOSED


# ---------------------------------------------------------------------------
# Stages cannot be skipped
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("run", [
    workflow.record_mid_year, workflow.submit_self_assessment,
    workflow.record_supervisor_review, workflow.record_committee_review,
    workflow.record_final_review, workflow.agree_development_plan,
    workflow.agree_training_plan,
])
def test_no_stage_can_be_skipped_from_goal_setting(cast, cycle, make_appraisal,
                                                   run):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    with pytest.raises(ValidationError):
        run(appraisal, cast["supervisor"])
    appraisal.refresh_from_db()
    assert appraisal.status == Status.GOAL_SETTING


def test_a_closed_appraisal_is_immovable(cast, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               [cast["committee"]], status=Status.CLOSED)
    for run in (workflow.agree_goals, workflow.record_mid_year,
                workflow.submit_self_assessment,
                workflow.record_supervisor_review):
        with pytest.raises(ValidationError):
            run(appraisal, cast["supervisor"])
    appraisal.refresh_from_db()
    assert appraisal.status == Status.CLOSED


# ---------------------------------------------------------------------------
# Going backwards
# ---------------------------------------------------------------------------
def test_an_appraisal_can_be_returned_to_an_earlier_stage(cast, cycle,
                                                          make_appraisal):
    """
    Real appraisals go back. A process that cannot will either be approved
    dishonestly or will leave the system for email, where nothing is recorded.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SUPERVISOR_REVIEW)
    workflow.return_to(appraisal, cast["supervisor"], Status.SELF_ASSESSMENT,
                       "Please expand on the second objective.")
    assert appraisal.status == Status.SELF_ASSESSMENT
    row = appraisal.audit_entries.filter(action="returned").first()
    assert "expand" in row.remarks


def test_returning_forwards_is_refused(cast, cycle, make_appraisal):
    """`return_to` is not a shortcut past the stages in between."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    with pytest.raises(ValidationError):
        workflow.return_to(appraisal, cast["supervisor"], Status.FINAL_REVIEW,
                           "Skipping ahead to the end.")


def test_a_return_needs_a_real_reason(cast, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SUPERVISOR_REVIEW)
    with pytest.raises(ValidationError):
        workflow.return_to(appraisal, cast["supervisor"],
                           Status.SELF_ASSESSMENT, "no")


def test_a_closed_appraisal_cannot_be_returned_only_reopened(cast, cycle,
                                                             make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               [cast["committee"]], status=Status.CLOSED)
    with pytest.raises(ValidationError):
        workflow.return_to(appraisal, cast["hr"], Status.FINAL_REVIEW,
                           "Trying to reopen the wrong way.")

    workflow.reopen(appraisal, cast["hr"], "A factual correction is needed.")
    assert appraisal.status == Status.FINAL_REVIEW
    assert appraisal.closed_at is None


def test_reopening_an_open_appraisal_is_refused(cast, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.FINAL_REVIEW)
    with pytest.raises(ValidationError):
        workflow.reopen(appraisal, cast["hr"], "It is not closed yet.")


def test_creation_is_recorded_once_however_often_it_is_called(cast, cycle,
                                                              make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    workflow.record_creation(appraisal, cast["hr"])
    workflow.record_creation(appraisal, cast["hr"])
    assert appraisal.audit_entries.filter(action="created").count() == 1


def test_one_appraisal_per_person_per_cycle(cast, cycle, make_appraisal):
    from django.db import IntegrityError

    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    with pytest.raises(IntegrityError):
        Appraisal.objects.create(cycle=cycle, employee=cast["employee"],
                                 supervisor=cast["supervisor"])
