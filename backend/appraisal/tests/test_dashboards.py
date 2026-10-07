"""
Dashboards and reports (APM-02).

Role blocks are ADDITIVE and chosen by the SERVER — a supervisor is also
somebody with their own appraisal, and HR are too. Replacing the personal block
with the managerial one is why people keep a second list.
"""
import csv
import io

import pytest

from appraisal import dashboards
from appraisal.models import Appraisal, TrainingPlan

from .conftest import APPRAISALS

pytestmark = pytest.mark.django_db

Status = Appraisal.Status
SLUGS = list(dashboards.REPORTS)


# ---------------------------------------------------------------------------
# Perspectives
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("who,perspective", [
    ("employee", "employee"),
    ("supervisor", "manager"),
    ("hr", "hr"),
    ("admin", "hr"),
])
def test_the_dashboard_answers_in_the_callers_perspective(cast, auth, cycle,
                                                          make_appraisal, who,
                                                          perspective):
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast[who]).get(f"{APPRAISALS}dashboard/").data
    assert body["perspective"] == perspective


def test_an_employee_gets_their_own_block_and_nobody_elses(cast, auth, cycle,
                                                           make_appraisal):
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    make_appraisal(cycle, cast["peer"], cast["supervisor"])

    body = auth(cast["employee"]).get(f"{APPRAISALS}dashboard/").data
    assert "employee" in body
    assert "manager" not in body
    assert "hr" not in body


def test_a_manager_keeps_their_own_block_alongside_the_team(cast, auth, cycle,
                                                            make_appraisal):
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    make_appraisal(cycle, cast["supervisor"], cast["hr"])

    body = auth(cast["supervisor"]).get(f"{APPRAISALS}dashboard/").data
    assert body["employee"]["current_appraisal"] is not None
    assert body["manager"]["team_size"] == 1


def test_hr_gets_every_block(cast, auth, cycle, make_appraisal):
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast["hr"]).get(f"{APPRAISALS}dashboard/").data
    assert {"employee", "manager", "hr"} <= set(body)


# ---------------------------------------------------------------------------
# Employee block
# ---------------------------------------------------------------------------
def test_the_employee_block_carries_everything_specified(cast, auth, cycle,
                                                         make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.DEVELOPMENT_PLAN)
    body = auth(cast["employee"]).get(
        f"{APPRAISALS}dashboard/").data["employee"]
    for key in ("goals", "development_plan", "training_recommendations",
                "stage", "goal_weight_total"):
        assert key in body, key
    assert len(body["goals"]) == 2
    assert body["goal_weight_total"] == 100


def test_the_employee_block_says_when_it_is_their_turn(cast, auth, cycle,
                                                       make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    body = auth(cast["employee"]).get(
        f"{APPRAISALS}dashboard/").data["employee"]
    assert body["awaiting_me"] is True


def test_the_employee_block_is_empty_but_valid_with_no_appraisal(cast, auth):
    body = auth(cast["employee"]).get(
        f"{APPRAISALS}dashboard/").data["employee"]
    assert body["current_appraisal"] is None
    assert body["goals"] == []


# ---------------------------------------------------------------------------
# Manager block
# ---------------------------------------------------------------------------
def test_pending_reviews_is_what_is_actually_with_the_manager(cast, auth,
                                                              cycle,
                                                              make_appraisal):
    make_appraisal(cycle, cast["employee"], cast["supervisor"],
                   status=Status.SUPERVISOR_REVIEW)
    make_appraisal(cycle, cast["peer"], cast["supervisor"],
                   status=Status.SELF_ASSESSMENT)   # with the employee, not them

    body = auth(cast["supervisor"]).get(
        f"{APPRAISALS}dashboard/").data["manager"]
    assert body["team_size"] == 2
    assert body["pending_reviews"] == 1


def test_the_manager_block_shows_every_stage_including_empty_ones(
        cast, auth, cycle, make_appraisal):
    """A stage that vanishes when empty makes a cycle look further along than
    it is; "nothing is with the committee" is information."""
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    stages = auth(cast["supervisor"]).get(
        f"{APPRAISALS}dashboard/").data["manager"]["by_stage"]
    assert len(stages) == len(Appraisal.LADDER)
    assert any(row["count"] == 0 for row in stages)


def test_development_needs_are_aggregated_across_the_team(cast, auth, cycle,
                                                          make_appraisal,
                                                          competencies):
    """A count of development ACTIONS by area — a shared gap made visible, not a
    judgement of individuals."""
    from appraisal.models import DevelopmentPlan

    for user in (cast["employee"], cast["peer"]):
        appraisal = make_appraisal(cycle, user, cast["supervisor"],
                                   status=Status.DEVELOPMENT_PLAN)
        DevelopmentPlan.objects.create(
            appraisal=appraisal, area="Presentation",
            action="Lead a briefing.", competency=competencies["communication"])

    needs = auth(cast["supervisor"]).get(
        f"{APPRAISALS}dashboard/").data["manager"]["development_needs"]
    assert needs[0]["area"] == "Communication"
    assert needs[0]["count"] == 2


# ---------------------------------------------------------------------------
# HR block
# ---------------------------------------------------------------------------
def test_the_hr_block_carries_everything_specified(cast, auth, cycle,
                                                   make_appraisal):
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast["hr"]).get(f"{APPRAISALS}dashboard/").data["hr"]
    for key in ("cycle_completion", "by_department", "review_status",
                "training_needs", "promotion_readiness", "succession"):
        assert key in body, key


def test_cycle_completion_counts_closed_appraisals(cast, auth, cycle,
                                                   make_appraisal):
    make_appraisal(cycle, cast["employee"], cast["supervisor"],
                   [cast["committee"]], status=Status.CLOSED)
    make_appraisal(cycle, cast["peer"], cast["supervisor"])

    body = auth(cast["hr"]).get(
        f"{APPRAISALS}dashboard/").data["hr"]["cycle_completion"]
    assert body["total"] == 2
    assert body["closed"] == 1
    assert body["percent"] == 50


def test_training_needs_are_summarised_for_hr(cast, auth, cycle,
                                              make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.TRAINING_PLAN)
    TrainingPlan.objects.create(appraisal=appraisal, title="Advanced Excel",
                                priority=TrainingPlan.Priority.HIGH)
    body = auth(cast["hr"]).get(
        f"{APPRAISALS}dashboard/").data["hr"]["training_needs"]
    assert body["total"] == 1
    assert body["identified"] == 1
    assert body["top_requests"][0]["title"] == "Advanced Excel"


def test_completion_is_zero_rather_than_a_crash_on_an_empty_cycle(cast, auth):
    body = auth(cast["hr"]).get(f"{APPRAISALS}dashboard/").data["hr"]
    assert body["cycle_completion"]["percent"] == 0


def test_a_stray_scope_cannot_narrow_the_dashboard(cast, auth, cycle,
                                                   make_appraisal):
    """
    The dashboard applies its own view. A `scope` left on the URL from a list
    must not silently shrink it — that is a bug nobody can see, because the
    page just quietly shows less.
    """
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    plain = auth(cast["hr"]).get(f"{APPRAISALS}dashboard/").data
    scoped = auth(cast["hr"]).get(f"{APPRAISALS}dashboard/",
                                  {"scope": "mine"}).data
    assert (plain["hr"]["cycle_completion"]["total"]
            == scoped["hr"]["cycle_completion"]["total"] == 1)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def test_all_six_reports_are_offered(cast, auth):
    slugs = [row["slug"] for row in auth(cast["hr"]).get(
        f"{APPRAISALS}reports/").data]
    assert slugs == ["appraisal-summary", "department-summary",
                     "goal-completion", "training-needs", "development-plans",
                     "promotion-readiness"]


@pytest.mark.parametrize("slug", SLUGS)
def test_every_report_returns_the_shared_envelope(cast, auth, cycle,
                                                  make_appraisal, slug):
    make_appraisal(cycle, cast["employee"], cast["supervisor"],
                   status=Status.DEVELOPMENT_PLAN)
    body = auth(cast["hr"]).get(f"{APPRAISALS}reports/{slug}/").data
    assert body["columns"] and isinstance(body["rows"], list)
    keys = {c["key"] for c in body["columns"]}
    for row in body["rows"]:
        assert keys <= set(row), f"{slug}: missing {keys - set(row)}"


@pytest.mark.parametrize("slug", SLUGS)
def test_every_report_exports_as_csv(cast, auth, cycle, make_appraisal, slug):
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    response = auth(cast["hr"]).get(f"{APPRAISALS}reports/{slug}/",
                                    {"export": "csv"})
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    json_body = auth(cast["hr"]).get(f"{APPRAISALS}reports/{slug}/").data
    assert rows[0] == [c["label"] for c in json_body["columns"]]


@pytest.mark.parametrize("slug", SLUGS)
def test_every_report_exports_as_pdf(cast, auth, cycle, make_appraisal, slug):
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    response = auth(cast["hr"]).get(f"{APPRAISALS}reports/{slug}/",
                                    {"export": "pdf"})
    assert response.status_code == 200, slug
    assert bytes(response.content)[:5] == b"%PDF-"


@pytest.mark.parametrize("slug", SLUGS)
def test_a_report_is_scoped_like_the_screen(cast, auth, cycle, make_appraisal,
                                            slug):
    """A summary is a disclosure like any other."""
    make_appraisal(cycle, cast["peer"], cast["supervisor"])
    body = auth(cast["outsider"]).get(f"{APPRAISALS}reports/{slug}/",
                                      {"export": "csv"}).content.decode("utf-8-sig")
    assert cast["peer"].get_full_name() not in body


def test_an_unknown_report_is_a_404(cast, auth):
    assert auth(cast["hr"]).get(
        f"{APPRAISALS}reports/nonsense/").status_code == 404


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------
def test_needs_me_pairs_each_stage_with_whose_turn_it_is(cast, auth, cycle,
                                                         make_appraisal):
    mine = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                          status=Status.SELF_ASSESSMENT)
    theirs = make_appraisal(cycle, cast["peer"], cast["supervisor"],
                            status=Status.SUPERVISOR_REVIEW)

    employee_queue = {row["id"] for row in auth(cast["employee"]).get(
        APPRAISALS, {"scope": "needs_me"}).data["results"]}
    supervisor_queue = {row["id"] for row in auth(cast["supervisor"]).get(
        APPRAISALS, {"scope": "needs_me"}).data["results"]}

    assert str(mine.id) in employee_queue
    assert str(theirs.id) not in employee_queue
    assert str(theirs.id) in supervisor_queue
