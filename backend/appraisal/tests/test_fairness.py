"""
The fairness boundaries (APM-02).

THIS IS THE MOST IMPORTANT FILE IN THE MODULE.

The specification asks for competency RATINGS and promotion READINESS while also
mandating no scoring, no ranking, no composite index and no leaderboard. Those
are not in conflict once the line is drawn in the right place, and this file is
where that line is written down and enforced:

    A PERSON may form a judgement and record it, with their reasons.
    THE SYSTEM may never form one, aggregate one, or order people by one.

So a supervisor rating somebody "Exceeds Expectations" with a written
justification is the process working. A field called `overall_score`, an average
of competency levels, or a list of employees sorted by anything, is the process
being replaced by a machine that cannot be argued with.

Every test below asserts an ABSENCE. Absences are exactly what rots without
tests: nobody notices the day a composite appears, because nothing fails.
"""
import pytest

from appraisal import dashboards
from appraisal.models import (
    Appraisal, AppraisalCycle, CompetencyRating, DevelopmentPlan, Goal,
    TrainingPlan,
)

from .conftest import APPRAISALS

pytestmark = pytest.mark.django_db

Status = Appraisal.Status
Readiness = Appraisal.PromotionReadiness

# Anything shaped like a machine-made judgement about a person.
#
# Precise rather than blunt on purpose. A bare "index" would catch
# `stage_index` — which is a POSITION IN THE WORKFLOW LADDER (stage 4 of 10), not
# a measure of anybody — and a guard that fires on legitimate fields gets
# loosened by the next person rather than tightened. So the forbidden terms name
# the thing actually being prohibited.
FORBIDDEN = ("score", "rating_total", "overall", "composite", "percentile",
             "rank", "grade", "band", "leaderboard", "points",
             "performance_index", "productivity", "weighted_achievement")

# `rating` alone is legitimate — CompetencyRating is a human act. What is
# forbidden is an AGGREGATE of ratings.
AGGREGATES = ("average", "mean", "total_rating", "aggregate_rating",
              "overall_rating", "final_score")

# Names that contain a forbidden substring but are demonstrably not judgements.
# Listed explicitly so each exemption is a decision somebody can see and argue
# with, rather than a hole in the pattern.
ALLOWED = {
    "stage_index",      # which of the ten stages, 1-10
    "total_stages",     # nine
}


# ---------------------------------------------------------------------------
# No composite exists anywhere in the data model
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model", [
    Appraisal, Goal, CompetencyRating, DevelopmentPlan, TrainingPlan,
    AppraisalCycle,
])
def test_no_model_carries_a_score_shaped_field(model):
    for field in model._meta.get_fields():
        name = field.name.lower()
        if name in ALLOWED:
            continue
        for word in FORBIDDEN:
            assert word not in name, f"{model.__name__}.{field.name}"


def test_the_appraisal_has_no_overall_outcome_field():
    """
    The outcome of an appraisal here is prose, rated competencies with their
    reasoning, and plans. If an organisation later wants a grade, that is a
    decision to take openly with the people it affects — not one this module
    makes for them by having the column ready.
    """
    fields = {f.name for f in Appraisal._meta.get_fields()}
    for absent in ("overall_rating", "final_score", "grade", "band",
                   "performance_index"):
        assert absent not in fields, absent


def test_competency_levels_are_words_not_numbers():
    """
    Stored as 1-5 integers, somebody averages them within a month and produces
    the composite this module may not have. Words stay ordered and comparable by
    a human while making that average a deliberate act rather than a convenience.
    """
    values = [value for value, _ in CompetencyRating.Level.choices]
    assert values == ["needs_development", "developing", "meets", "exceeds",
                      "outstanding"]
    for value in values:
        assert not value.isdigit()

    field = CompetencyRating._meta.get_field("level")
    assert field.get_internal_type() == "CharField"


def test_a_competency_rating_cannot_be_recorded_without_reasoning(
        cast, auth, cycle, make_appraisal, competencies):
    """A level with no reasoning is a number in disguise."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    response = auth(cast["employee"]).post(f"{APPRAISALS}{appraisal.id}/rate/", {
        "competency": str(competencies["teamwork"].id),
        "level": "exceeds", "comment": "good", "role": "employee",
    }, format="json")
    assert response.status_code == 400
    assert "comment" in response.data


# ---------------------------------------------------------------------------
# Self and supervisor views coexist; neither is averaged away
# ---------------------------------------------------------------------------
def test_a_self_rating_and_a_supervisor_rating_both_survive(
        cast, auth, cycle, make_appraisal, competencies):
    """
    The gap between the two is usually the most useful thing in the
    conversation. Averaging them destroys exactly that.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    teamwork = competencies["teamwork"]

    auth(cast["employee"]).post(f"{APPRAISALS}{appraisal.id}/rate/", {
        "competency": str(teamwork.id), "level": "outstanding",
        "comment": "I led the cross-team migration end to end.",
        "role": "employee"}, format="json")

    appraisal.self_assessment = "My account of the year."
    appraisal.save(update_fields=["self_assessment"])
    from appraisal import workflow
    workflow.submit_self_assessment(appraisal, cast["employee"])

    auth(cast["supervisor"]).post(f"{APPRAISALS}{appraisal.id}/rate/", {
        "competency": str(teamwork.id), "level": "meets",
        "comment": "Solid contribution; the migration was a team effort.",
        "role": "supervisor"}, format="json")

    rows = CompetencyRating.objects.filter(appraisal=appraisal,
                                           competency=teamwork)
    assert rows.count() == 2
    assert {r.level for r in rows} == {"outstanding", "meets"}


def test_the_detail_payload_never_aggregates_competency_ratings(
        cast, auth, cycle, make_appraisal, competencies):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    auth(cast["employee"]).post(f"{APPRAISALS}{appraisal.id}/rate/", {
        "competency": str(competencies["teamwork"].id), "level": "exceeds",
        "comment": "Detailed reasoning recorded here.", "role": "employee",
    }, format="json")

    body = auth(cast["employee"]).get(f"{APPRAISALS}{appraisal.id}/").data
    for key in body:
        if key in ALLOWED:
            continue
        for word in FORBIDDEN + AGGREGATES:
            assert word not in key.lower(), key
    # The ratings arrive as a LIST of individual human judgements.
    assert isinstance(body["competency_ratings"], list)
    assert all("comment" in row for row in body["competency_ratings"])


def test_goal_weights_are_never_turned_into_a_performance_figure(
        cast, auth, cycle, make_appraisal):
    """
    `goal_weight_total` exists because the process requires 100% — a
    completeness check on the PLAN. It must never be joined to progress to
    manufacture a weighted achievement score.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast["employee"]).get(f"{APPRAISALS}{appraisal.id}/").data
    assert body["goal_weight_total"] == 100
    for key in body:
        assert "weighted" not in key.lower()
        assert "achievement_score" not in key.lower()


# ---------------------------------------------------------------------------
# Nothing orders people by performance
# ---------------------------------------------------------------------------
def test_the_manager_dashboard_lists_the_team_alphabetically(
        cast, auth, cycle, make_appraisal):
    """
    A team list sorted by anything else is a ranking, whatever the header says,
    and the person at the bottom of it will be asked about it.
    """
    make_appraisal(cycle, cast["peer"], cast["supervisor"],
                   status=Status.CLOSED, actor=cast["supervisor"])
    make_appraisal(cycle, cast["employee"], cast["supervisor"])

    team = auth(cast["supervisor"]).get(
        f"{APPRAISALS}dashboard/").data["manager"]["team"]
    names = [row["employee"] for row in team]
    assert names == sorted(names, key=str.lower)


def test_no_dashboard_block_carries_a_score(cast, auth, cycle, make_appraisal):
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast["hr"]).get(f"{APPRAISALS}dashboard/").data

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key not in ALLOWED:
                    for word in FORBIDDEN:
                        assert word not in key.lower(), f"{path}.{key}"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    walk(body)


def test_promotion_readiness_lists_only_what_a_human_recorded(
        cast, auth, cycle, make_appraisal):
    """
    Not a computed readiness score and not a shortlist ordered by anything.
    Somebody with no recommendation is ABSENT rather than listed as a negative —
    "not considered" and "not ready" are different, and conflating them puts a
    negative on every record that never reached the question.
    """
    recommended = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                                 status=Status.FINAL_REVIEW)
    recommended.promotion_readiness = Readiness.READY
    recommended.promotion_rationale = "Operating a grade above for two cycles."
    recommended.save(update_fields=["promotion_readiness",
                                    "promotion_rationale"])
    make_appraisal(cycle, cast["peer"], cast["supervisor"])   # no recommendation

    block = auth(cast["hr"]).get(
        f"{APPRAISALS}dashboard/").data["hr"]["promotion_readiness"]
    names = [row["employee"] for row in block]
    assert cast["employee"].get_full_name() in names
    assert cast["peer"].get_full_name() not in names
    assert block[0]["rationale"]      # the reasoning travels with the name


def test_the_promotion_report_is_alphabetical_and_carries_rationale(
        cast, auth, cycle, make_appraisal):
    for user in (cast["peer"], cast["employee"]):
        appraisal = make_appraisal(cycle, user, cast["supervisor"],
                                   status=Status.FINAL_REVIEW)
        appraisal.promotion_readiness = Readiness.READY
        appraisal.promotion_rationale = f"Reasoning for {user.get_full_name()}."
        appraisal.save(update_fields=["promotion_readiness",
                                      "promotion_rationale"])

    body = auth(cast["hr"]).get(
        f"{APPRAISALS}reports/promotion-readiness/").data
    names = [row["employee"] for row in body["rows"]]
    assert names == sorted(names, key=str.lower)
    assert all(row["rationale"] for row in body["rows"])
    assert "computed, scored or ranked" in body["summary"]["note"].lower()


@pytest.mark.parametrize("slug", list(dashboards.REPORTS))
def test_no_report_carries_a_score_column(cast, auth, cycle, make_appraisal,
                                          slug):
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast["hr"]).get(f"{APPRAISALS}reports/{slug}/").data
    for column in body["columns"]:
        for word in FORBIDDEN:
            assert word not in column["key"].lower(), (slug, column["key"])
            assert word not in column["label"].lower(), (slug, column["label"])


@pytest.mark.parametrize("slug", list(dashboards.REPORTS))
def test_every_report_orders_people_by_name(cast, auth, cycle, make_appraisal,
                                            slug):
    for user in (cast["peer"], cast["employee"]):
        make_appraisal(cycle, user, cast["supervisor"])
    rows = auth(cast["hr"]).get(f"{APPRAISALS}reports/{slug}/").data["rows"]
    names = [row["employee"] for row in rows if "employee" in row]
    if names:
        assert names == sorted(names, key=str.lower), slug


# ---------------------------------------------------------------------------
# The process stays human
# ---------------------------------------------------------------------------
def test_no_transition_is_driven_by_task_evidence(cast, cycle, make_appraisal):
    """
    There is no path by which activity data moves an appraisal. Evidence informs
    the humans; the humans move the appraisal.
    """
    import ast
    import pathlib

    from appraisal import workflow

    source = pathlib.Path(workflow.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])

    assert "tasks" not in imported
    assert "evidence" not in imported
    for word in ("completion_percent", "on_time", "tasks_completed"):
        assert word not in source, word


def test_an_appraisal_cannot_advance_without_a_person_acting(cast, cycle,
                                                             make_appraisal):
    """
    Every transition takes an actor and records their name. There is no
    scheduled job, signal or evidence threshold that moves an appraisal on.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SUPERVISOR_REVIEW)
    for row in appraisal.audit_entries.all():
        assert row.actor_name, row.action
