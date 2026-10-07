"""
Phase APM-FINAL, Part 5 — end-to-end UAT.

One appraisal, walked through all ten stages by the four people who actually
touch it, through the HTTP API rather than the engine. Everything else in this
suite tests a rule in isolation; this asks the only question a UAT can: can
these four people, with their real permissions, complete a cycle together?

Each step asserts BOTH that the right person can act and that the wrong one
cannot, because a workflow that lets the correct actor through is only half of
what matters.
"""
import datetime

import pytest
from django.utils import timezone

from appraisal.models import Appraisal, AppraisalCycle, Goal
from .conftest import APPRAISALS, CYCLES

pytestmark = pytest.mark.django_db

Status = Appraisal.Status


@pytest.fixture
def uat(cast, auth, competencies):
    """HR opens a cycle and an appraisal. The starting point for every walk."""
    today = timezone.localdate()
    response = auth(cast["hr"]).post(CYCLES, {
        "name": "UAT FY 2083/84",
        "period_start": str(today - datetime.timedelta(days=180)),
        "period_end": str(today + datetime.timedelta(days=180)),
    }, format="json")
    assert response.status_code == 201, response.data
    cycle_id = response.data["id"]
    auth(cast["hr"]).post(f"{CYCLES}{cycle_id}/activate/")

    response = auth(cast["hr"]).post(APPRAISALS, {
        "cycle": cycle_id,
        "employee": str(cast["employee"].id),
        "supervisor": str(cast["supervisor"].id),
        "committee": [str(cast["committee"].id)],
    }, format="json")
    assert response.status_code == 201, response.data
    return {"cycle": cycle_id, "appraisal": response.data["id"],
            "competencies": competencies}


def detail(auth, user, appraisal_id):
    response = auth(user).get(f"{APPRAISALS}{appraisal_id}/")
    assert response.status_code == 200
    return response.data


# ---------------------------------------------------------------------------
# HR
# ---------------------------------------------------------------------------
def test_hr_alone_may_open_an_appraisal(cast, auth, cycle):
    for actor, expected in [("employee", 403), ("supervisor", 403),
                            ("hr", 201)]:
        response = auth(cast[actor]).post(APPRAISALS, {
            "cycle": str(cycle.id), "employee": str(cast["peer"].id),
            "supervisor": str(cast["supervisor"].id),
        }, format="json")
        assert response.status_code == expected, actor
        if expected == 201:
            Appraisal.objects.filter(pk=response.data["id"]).delete()


def test_nobody_supervises_or_reviews_their_own_appraisal(cast, auth, cycle):
    """The whole process rests on there being a second person in it."""
    same = auth(cast["hr"]).post(APPRAISALS, {
        "cycle": str(cycle.id), "employee": str(cast["employee"].id),
        "supervisor": str(cast["employee"].id),
    }, format="json")
    assert same.status_code == 400

    committee = auth(cast["hr"]).post(APPRAISALS, {
        "cycle": str(cycle.id), "employee": str(cast["employee"].id),
        "supervisor": str(cast["supervisor"].id),
        "committee": [str(cast["employee"].id)],
    }, format="json")
    assert committee.status_code == 400


# ---------------------------------------------------------------------------
# The full walk
# ---------------------------------------------------------------------------
def test_four_people_complete_one_appraisal_end_to_end(cast, auth, uat):
    appraisal_id = uat["appraisal"]
    competency = list(uat["competencies"].values())[0]

    # --- 1. Goal Setting: the EMPLOYEE writes their objectives -------------
    for objective, weight in [("Deliver the quarterly returns", 60),
                              ("Improve the archive process", 40)]:
        created = auth(cast["employee"]).post(
            f"{APPRAISALS}{appraisal_id}/goals/",
            {"objective": objective, "weight": weight,
             "target": "What success looks like."}, format="json")
        assert created.status_code == 201, created.data
        assert created.data["status"] == "draft"

    # An outsider can see none of this.
    assert auth(cast["outsider"]).get(
        f"{APPRAISALS}{appraisal_id}/").status_code == 404

    # --- 2. Submission: the employee hands them over ----------------------
    assert detail(auth, cast["employee"], appraisal_id)[
        "capabilities"]["can_submit_goals"] is True
    submitted = auth(cast["employee"]).post(
        f"{APPRAISALS}{appraisal_id}/submit-goals/")
    assert submitted.status_code == 200, submitted.data
    assert submitted.data["status"] == "goal_approval"

    # --- 3. Goal Approval: the SUPERVISOR accepts, the employee cannot -----
    assert auth(cast["employee"]).post(
        f"{APPRAISALS}{appraisal_id}/agree-goals/").status_code == 403
    approved = auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal_id}/agree-goals/")
    assert approved.status_code == 200, approved.data
    assert approved.data["status"] == "mid_year_review"
    assert {g["status"] for g in approved.data["goals"]} == {"approved"}

    # --- 4. Mid-year, then the objectives lock ----------------------------
    mid = auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal_id}/record-mid-year/")
    assert mid.status_code == 200
    assert {g["status"] for g in mid.data["goals"]} == {"locked"}

    # --- 5. Self assessment: the employee's words, and only theirs ---------
    assert auth(cast["supervisor"]).patch(
        f"{APPRAISALS}{appraisal_id}/",
        {"self_assessment": "Not the supervisor's to write."},
        format="json").status_code == 403
    assert auth(cast["employee"]).patch(
        f"{APPRAISALS}{appraisal_id}/",
        {"self_assessment": "## Achievements\nBoth objectives delivered."},
        format="json").status_code == 200

    # The employee rates themselves at their own stage.
    rated = auth(cast["employee"]).post(
        f"{APPRAISALS}{appraisal_id}/rate/",
        {"competency": str(competency.id), "level": "meets",
         "comment": "Consistent across the year, with detail here.",
         "role": "employee"}, format="json")
    assert rated.status_code == 200, rated.data

    # Evidence is cited by the person it is about.
    cited = auth(cast["employee"]).post(
        f"{APPRAISALS}{appraisal_id}/attach-evidence/",
        {"note": "Covers the returns objective."}, format="json")
    assert cited.status_code == 200
    assert len(cited.data["evidence_references"]) == 1

    assert auth(cast["employee"]).post(
        f"{APPRAISALS}{appraisal_id}/submit-self-assessment/"
    ).status_code == 200

    # --- 6. Supervisor review ---------------------------------------------
    assert auth(cast["employee"]).patch(
        f"{APPRAISALS}{appraisal_id}/",
        {"supervisor_comments": "Not mine to write."},
        format="json").status_code == 403
    assert auth(cast["supervisor"]).patch(
        f"{APPRAISALS}{appraisal_id}/",
        {"supervisor_comments": "A solid year; specifics recorded here."},
        format="json").status_code == 200
    auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal_id}/rate/",
        {"competency": str(competency.id), "level": "exceeds",
         "comment": "Took on the archive work unasked.",
         "role": "supervisor"}, format="json")
    assert auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal_id}/record-supervisor-review/"
    ).status_code == 200

    # Both ratings coexist — the gap between them is the conversation.
    body = detail(auth, cast["employee"], appraisal_id)
    levels = {r["rated_by_role"]: r["level"] for r in body["competency_ratings"]}
    assert levels == {"employee": "meets", "supervisor": "exceeds"}

    # --- 7. Committee review ----------------------------------------------
    assert auth(cast["committee"]).patch(
        f"{APPRAISALS}{appraisal_id}/",
        {"committee_comments": "The committee agrees with the supervisor."},
        format="json").status_code == 200
    assert auth(cast["committee"]).post(
        f"{APPRAISALS}{appraisal_id}/record-committee-review/"
    ).status_code == 200

    # --- 8. Final review, with a promotion recommendation -----------------
    assert auth(cast["supervisor"]).patch(
        f"{APPRAISALS}{appraisal_id}/",
        {"final_summary": "Objectives met in full.",
         "promotion_readiness": "ready_with_development",
         "promotion_rationale": "Ready once they have run a project alone.",
         "successor_for": "Senior Officer"}, format="json").status_code == 200
    assert auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal_id}/record-final-review/"
    ).status_code == 200

    # --- 9. Development plan ----------------------------------------------
    assert auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal_id}/development-plan/",
        {"area": "Presentation skills",
         "action": "Lead two team briefings next quarter."},
        format="json").status_code == 201
    assert auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal_id}/agree-development-plan/"
    ).status_code == 200

    # --- 10. Training plan, decided by HR ---------------------------------
    training = auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal_id}/training-plan/",
        {"title": "Shadow the close", "kind": "mentorship",
         "mentor": str(cast["peer"].id),
         "justification": "Practical exposure to the finance calendar."},
        format="json")
    assert training.status_code == 201, training.data

    # A supervisor cannot commit the organisation's budget.
    assert auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal_id}/training-plan/{training.data['id']}/decide/",
        {"status": "approved"}, format="json").status_code == 403
    assert auth(cast["hr"]).post(
        f"{APPRAISALS}{appraisal_id}/training-plan/{training.data['id']}/decide/",
        {"status": "approved", "decision_note": "Budgeted for Q2."},
        format="json").status_code == 200

    closed = auth(cast["supervisor"]).post(
        f"{APPRAISALS}{appraisal_id}/agree-training-plan/")
    assert closed.status_code == 200
    assert closed.data["status"] == "closed"

    # --- The record that survives -----------------------------------------
    final = detail(auth, cast["employee"], appraisal_id)
    assert final["promotion_readiness_label"] == "Ready With Development"
    assert final["self_assessment"]
    assert final["supervisor_comments"]
    assert final["committee_comments"]
    assert final["final_summary"]
    assert len(final["timeline"]) >= 10

    # A closed appraisal is permanent: no edits, by anybody.
    for actor in ("employee", "supervisor", "hr"):
        assert auth(cast[actor]).patch(
            f"{APPRAISALS}{appraisal_id}/",
            {"final_summary": "Rewritten."},
            format="json").status_code == 403, actor


def test_the_committee_sees_only_what_it_was_placed_on(cast, auth, uat):
    """Sitting on one committee grants no view of any other appraisal."""
    other = auth(cast["hr"]).post(APPRAISALS, {
        "cycle": uat["cycle"], "employee": str(cast["peer"].id),
        "supervisor": str(cast["supervisor"].id),
    }, format="json")
    assert other.status_code == 201

    visible = auth(cast["committee"]).get(
        f"{APPRAISALS}?scope=committee").data
    rows = visible["results"] if isinstance(visible, dict) else visible
    assert [r["id"] for r in rows] == [uat["appraisal"]]
    assert auth(cast["committee"]).get(
        f"{APPRAISALS}{other.data['id']}/").status_code == 404


def test_hr_sees_the_round_and_the_employee_sees_only_themselves(cast, auth,
                                                                 uat):
    hr_view = auth(cast["hr"]).get(f"{APPRAISALS}dashboard/").data
    assert "hr" in hr_view
    assert hr_view["hr"]["cycle_completion"]["total"] >= 1

    employee_view = auth(cast["employee"]).get(f"{APPRAISALS}dashboard/").data
    assert "hr" not in employee_view
    assert "manager" not in employee_view
    assert employee_view["employee"]["current_appraisal"] == uat["appraisal"]
