"""
Phase APM-03b — the two model deltas the appraisal UX required.

Both exist because the UI could not honestly render the old shape:

  * a Ready / Ready With Development / Development Required selector cannot be
    driven by a boolean, and "not considered" is a fourth state that must stay
    distinguishable from "development required";
  * a Training / Certification / Mentorship / On-the-job filter cannot be driven
    by a model where everything is training.

Most of these assert an ABSENCE — no ordering by readiness, no count of the
people who were never considered, no numeric value anywhere near either field.
The fields are the two places in this module where a ranking would be easiest to
introduce and hardest to notice.
"""
import pytest

from appraisal.models import Appraisal, TrainingPlan
from .conftest import APPRAISALS

pytestmark = pytest.mark.django_db

Status = Appraisal.Status
Readiness = Appraisal.PromotionReadiness
Kind = TrainingPlan.Kind


# ---------------------------------------------------------------------------
# Promotion readiness
# ---------------------------------------------------------------------------
def test_readiness_has_exactly_the_three_recorded_answers():
    """
    Three values, and the absence is NOT one of them.

    "Not considered" is modelled as the empty string rather than a fourth
    choice, so nothing can filter for it, count it or put it in a chart legend
    beside the real answers — which is how an absence turns into a verdict.
    """
    assert [value for value, _ in Readiness.choices] == [
        "ready", "ready_with_development", "development_required"]
    assert Appraisal().promotion_readiness == ""


def test_none_of_the_three_carries_a_number():
    """
    Stored as words. Stored as 1/2/3 somebody averages them within a month, and
    a "mean promotion readiness of 2.4" is exactly the composite this module is
    forbidden to produce.
    """
    for value, label in Readiness.choices:
        assert not any(ch.isdigit() for ch in value)
        assert not any(ch.isdigit() for ch in label)


@pytest.mark.parametrize("readiness", [
    Readiness.READY, Readiness.READY_WITH_DEVELOPMENT,
    Readiness.DEVELOPMENT_REQUIRED,
])
def test_every_answer_needs_its_rationale_including_the_negative(
        cast, cycle, make_appraisal, readiness):
    """
    The old boolean's guard fired "in either direction". With three answers the
    one that most needs a reason is DEVELOPMENT_REQUIRED — a no with no stated
    development is a verdict nobody can act on or appeal.
    """
    from appraisal import workflow
    from rest_framework.exceptions import ValidationError

    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.FINAL_REVIEW)
    appraisal.final_summary = "A full year recorded."
    appraisal.promotion_readiness = readiness
    appraisal.save(update_fields=["final_summary", "promotion_readiness"])

    with pytest.raises(ValidationError):
        workflow.record_final_review(appraisal, cast["supervisor"])

    appraisal.promotion_rationale = "The reasoning, recorded."
    appraisal.save(update_fields=["promotion_rationale"])
    workflow.record_final_review(appraisal, cast["supervisor"])
    assert appraisal.status == Status.DEVELOPMENT_PLAN


def test_leaving_readiness_unset_still_closes_the_review(cast, cycle,
                                                         make_appraisal):
    """Not every appraisal reaches the promotion question. Not reaching it must
    not block the round."""
    from appraisal import workflow
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.FINAL_REVIEW)
    appraisal.final_summary = "A full year recorded."
    appraisal.save(update_fields=["final_summary"])
    workflow.record_final_review(appraisal, cast["supervisor"])
    assert appraisal.promotion_readiness == ""


def test_only_the_finaliser_may_write_readiness(cast, auth, cycle,
                                                make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.FINAL_REVIEW)
    refused = auth(cast["employee"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"promotion_readiness": Readiness.READY}, format="json")
    assert refused.status_code == 403
    appraisal.refresh_from_db()
    assert appraisal.promotion_readiness == ""


def test_the_dashboard_reports_the_answer_not_a_yes(cast, auth, cycle,
                                                    make_appraisal):
    """The middle answer has to survive the trip to the screen. Collapsed back
    to a yes on the way out, the field would be a boolean again."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.FINAL_REVIEW)
    appraisal.promotion_readiness = Readiness.READY_WITH_DEVELOPMENT
    appraisal.promotion_rationale = "Ready once they have run a project alone."
    appraisal.save(update_fields=["promotion_readiness",
                                  "promotion_rationale"])

    block = auth(cast["hr"]).get(
        f"{APPRAISALS}dashboard/").data["hr"]["promotion_readiness"]
    row = next(r for r in block
               if r["employee"] == cast["employee"].get_full_name())
    assert row["readiness"] == "ready_with_development"
    assert row["readiness_label"] == "Ready With Development"


def test_the_readiness_list_is_alphabetical_not_best_first(cast, auth, cycle,
                                                           make_appraisal):
    """
    Ordering three ordered answers best-first is a ranking whatever the column
    is called. The list is by name, and the least favourable answer is given to
    the person who sorts FIRST so that a readiness ordering would visibly
    differ from what the server sent.
    """
    pairs = [(cast["employee"], Readiness.DEVELOPMENT_REQUIRED),
             (cast["peer"], Readiness.READY)]
    for user, readiness in pairs:
        appraisal = make_appraisal(cycle, user, cast["supervisor"],
                                   status=Status.FINAL_REVIEW)
        appraisal.promotion_readiness = readiness
        appraisal.promotion_rationale = "Recorded reasoning."
        appraisal.save(update_fields=["promotion_readiness",
                                      "promotion_rationale"])

    rows = auth(cast["hr"]).get(
        f"{APPRAISALS}reports/promotion-readiness/").data["rows"]
    names = [r["employee"] for r in rows]
    assert names == sorted(names, key=str.lower)
    assert {r["readiness"] for r in rows} == {"Development Required", "Ready"}


def test_nobody_is_counted_as_not_considered(cast, auth, cycle,
                                             make_appraisal):
    """
    The per-answer counts cover the three recorded answers only. A "Not
    Considered: 42" tile sits beside the real answers and reads as a fourth
    verdict on 42 people, which is precisely the reading the blank exists to
    prevent.
    """
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    counts = auth(cast["hr"]).get(
        f"{APPRAISALS}dashboard/").data["hr"]["promotion_by_readiness"]
    assert [row["value"] for row in counts] == [
        "ready", "ready_with_development", "development_required"]
    assert all(row["count"] == 0 for row in counts)


# ---------------------------------------------------------------------------
# Training plan kind
# ---------------------------------------------------------------------------
def test_the_four_kinds_are_the_ones_the_specification_names():
    assert [value for value, _ in Kind.choices] == [
        "training", "certification", "mentorship", "on_the_job"]


def test_an_existing_item_with_no_kind_reads_as_training(cast, cycle,
                                                         make_appraisal):
    """The migration's default. Everything recorded before this field existed
    was a course, so that is what it must still say."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.TRAINING_PLAN)
    row = TrainingPlan.objects.create(appraisal=appraisal, title="SQL course")
    assert row.kind == Kind.TRAINING


def test_a_mentorship_can_be_recorded_with_its_mentor(cast, auth, cycle,
                                                      make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.TRAINING_PLAN)
    response = auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal.id}/training-plan/",
        {"title": "Pair on the quarterly close", "kind": "mentorship",
         "mentor": str(cast["peer"].id),
         "justification": "Wants exposure to the finance calendar."},
        format="json")
    assert response.status_code == 201, response.data
    assert response.data["kind_label"] == "Mentorship"
    assert response.data["mentor"] == cast["peer"].id
    # The name is snapshot beside the reference: a mentor who leaves must not
    # turn a recorded pairing into a blank cell on somebody's appraisal.
    assert response.data["mentor_name"] == cast["peer"].get_full_name()


def test_a_mentor_on_a_course_is_refused(cast, auth, cycle, make_appraisal):
    """A field the reader has to guess the meaning of is worse than no field."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.TRAINING_PLAN)
    response = auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal.id}/training-plan/",
        {"title": "Advanced Excel", "kind": "certification",
         "mentor": str(cast["peer"].id)}, format="json")
    assert response.status_code == 400
    assert "mentor" in response.data


def test_hr_sees_the_shape_of_what_was_asked_for(cast, auth, cycle,
                                                 make_appraisal):
    """
    The reason the field exists. Before it, HR's training list silently mixed
    things that need a budget with things that need a colleague's time, and the
    total was quoted as a training spend.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.TRAINING_PLAN)
    for title, kind in [("ISO 27001", Kind.CERTIFICATION),
                        ("Shadow the close", Kind.MENTORSHIP),
                        ("Run the standup", Kind.ON_THE_JOB)]:
        TrainingPlan.objects.create(appraisal=appraisal, title=title, kind=kind)

    block = auth(cast["hr"]).get(
        f"{APPRAISALS}dashboard/").data["hr"]["training_needs"]["by_kind"]
    counts = {row["kind"]: row["count"] for row in block}
    assert counts == {"training": 0, "certification": 1, "mentorship": 1,
                      "on_the_job": 1}


def test_the_training_report_names_the_type_and_the_mentor(cast, auth, cycle,
                                                           make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.TRAINING_PLAN)
    TrainingPlan.objects.create(
        appraisal=appraisal, title="Shadow the close", kind=Kind.MENTORSHIP,
        mentor=cast["peer"], mentor_name=cast["peer"].get_full_name())

    body = auth(cast["hr"]).get(f"{APPRAISALS}reports/training-needs/").data
    assert {"kind", "mentor"} <= {c["key"] for c in body["columns"]}
    row = body["rows"][0]
    assert row["kind"] == "Mentorship"
    assert row["mentor"] == cast["peer"].get_full_name()


# ---------------------------------------------------------------------------
# HR dashboard: review delays and development plans (APM-03b completion)
# ---------------------------------------------------------------------------
def test_review_delays_name_the_record_and_who_holds_it(cast, auth, cycle,
                                                        make_appraisal):
    """
    A measure of the PROCESS — how long this record has waited — not of a
    person. It names the reviewer because a delay nobody owns is a delay nobody
    clears.
    """
    import datetime
    from django.utils import timezone
    from appraisal.models import Appraisal as A

    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SUPERVISOR_REVIEW)
    A.objects.filter(pk=appraisal.pk).update(
        updated_at=timezone.now() - datetime.timedelta(days=11))

    block = auth(cast["hr"]).get(f"{APPRAISALS}dashboard/").data["hr"]
    row = next(r for r in block["review_delays"]
               if r["employee"] == cast["employee"].get_full_name())
    assert row["days_waiting"] == 11
    assert row["stage"] == "Supervisor Review"
    assert row["with_whom"] == cast["supervisor"].get_full_name()
    assert block["longest_wait_days"] == 11


def test_review_delays_are_ordered_by_the_wait_not_by_the_person(
        cast, auth, cycle, make_appraisal):
    """
    The one ordering on this dashboard that is not a ranking of people. Longest
    wait first, because that is the record most likely to be blocking somebody —
    and the tie-break is alphabetical rather than anything about the employee.
    """
    import datetime
    from django.utils import timezone
    from appraisal.models import Appraisal as A

    for user, days in [(cast["employee"], 3), (cast["peer"], 14)]:
        appraisal = make_appraisal(cycle, user, cast["supervisor"],
                                   status=Status.SUPERVISOR_REVIEW)
        A.objects.filter(pk=appraisal.pk).update(
            updated_at=timezone.now() - datetime.timedelta(days=days))

    rows = auth(cast["hr"]).get(
        f"{APPRAISALS}dashboard/").data["hr"]["review_delays"]
    assert [r["days_waiting"] for r in rows] == sorted(
        [r["days_waiting"] for r in rows], reverse=True)
    assert rows[0]["employee"] == cast["peer"].get_full_name()


def test_a_record_nobody_is_waiting_on_is_not_a_delay(cast, auth, cycle,
                                                      make_appraisal):
    """Only the three REVIEW stages count. An appraisal sitting at goal setting
    is waiting on its objectives, not on a reviewer."""
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    block = auth(cast["hr"]).get(f"{APPRAISALS}dashboard/").data["hr"]
    assert block["review_delays"] == []
    assert block["longest_wait_days"] == 0


def test_development_plans_are_summarised_by_area_and_never_by_person(
        cast, auth, cycle, make_appraisal):
    """
    A count of development ACTIONS by area, so a shared gap across the
    organisation is visible. Nobody is named: "six people need presentation
    skills" is a training decision, and "these six people" is a list that would
    be read as a ranking.
    """
    from appraisal.models import DevelopmentPlan

    for user in (cast["employee"], cast["peer"]):
        appraisal = make_appraisal(cycle, user, cast["supervisor"],
                                   status=Status.DEVELOPMENT_PLAN)
        DevelopmentPlan.objects.create(
            appraisal=appraisal, area="Presentation skills",
            action="Lead two briefings next quarter.")

    block = auth(cast["hr"]).get(
        f"{APPRAISALS}dashboard/").data["hr"]["development_plans"]
    assert block["total"] >= 2
    top = next(r for r in block["top_areas"]
               if r["area"] == "Presentation skills")
    assert top["count"] == 2
    assert all("employee" not in row for row in block["top_areas"])
