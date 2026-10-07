"""
Appraisal RBAC.

Appraisal visibility is TIGHTER than anywhere else in this system, and these
tests exist to keep it that way. An appraisal holds somebody's self-assessment,
their manager's private view of them and a promotion recommendation — so
membership is explicit (named supervisor, named committee) rather than inferred
from a department. A department head is NOT entitled to the appraisal of
everybody in their department, only of the people they actually supervise.
"""
import pytest

from appraisal.models import Appraisal

from .conftest import APPRAISALS, CYCLES

pytestmark = pytest.mark.django_db

Status = Appraisal.Status


def ids(response):
    rows = response.data.get("results", response.data)
    return {row["id"] for row in rows}


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------
def test_an_employee_sees_only_their_own_appraisal(cast, auth, cycle,
                                                   make_appraisal):
    mine = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    theirs = make_appraisal(cycle, cast["peer"], cast["supervisor"])

    visible = ids(auth(cast["employee"]).get(APPRAISALS))
    assert str(mine.id) in visible
    assert str(theirs.id) not in visible


def test_a_supervisor_sees_the_people_they_supervise(cast, auth, cycle,
                                                     make_appraisal):
    mine = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    elsewhere = make_appraisal(cycle, cast["outsider"], cast["hr"])

    visible = ids(auth(cast["supervisor"]).get(APPRAISALS))
    assert str(mine.id) in visible
    assert str(elsewhere.id) not in visible


def test_a_department_head_is_not_entitled_to_their_whole_department(
        cast, auth, cycle, make_appraisal):
    """
    The distinction this module turns on. `supervisor` is the head of
    Engineering, and `peer` is in Engineering — but supervised by HR here. That
    appraisal is none of the head's business.
    """
    not_mine = make_appraisal(cycle, cast["peer"], cast["hr"])
    assert str(not_mine.id) not in ids(auth(cast["supervisor"]).get(APPRAISALS))
    assert auth(cast["supervisor"]).get(
        f"{APPRAISALS}{not_mine.id}/").status_code == 404


def test_a_committee_member_sees_the_appraisals_assigned_to_them(
        cast, auth, cycle, make_appraisal):
    assigned = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                              [cast["committee"]])
    unassigned = make_appraisal(cycle, cast["peer"], cast["supervisor"])

    visible = ids(auth(cast["committee"]).get(APPRAISALS))
    assert str(assigned.id) in visible
    assert str(unassigned.id) not in visible


@pytest.mark.parametrize("who", ["hr", "admin"])
def test_hr_and_admin_see_the_organisation(cast, auth, cycle, make_appraisal,
                                           who):
    a = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    b = make_appraisal(cycle, cast["outsider"], cast["hr"])
    assert {str(a.id), str(b.id)} <= ids(auth(cast[who]).get(APPRAISALS))


def test_an_unrelated_person_gets_404_not_403(cast, auth, cycle,
                                              make_appraisal):
    """404, because the queryset excludes it. A 403 would confirm the record
    exists, which is an enumeration oracle over people's appraisals."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    assert auth(cast["outsider"]).get(
        f"{APPRAISALS}{appraisal.id}/").status_code == 404


def test_appraisals_require_authentication(api):
    assert api.get(APPRAISALS).status_code in (401, 403)


# ---------------------------------------------------------------------------
# Who may open and administer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("who,expected", [
    ("employee", 403), ("supervisor", 403), ("hr", 201), ("admin", 201),
])
def test_only_hr_opens_an_appraisal(cast, auth, cycle, who, expected):
    response = auth(cast[who]).post(APPRAISALS, {
        "cycle": str(cycle.id), "employee": str(cast["peer"].id),
        "supervisor": str(cast["supervisor"].id),
    }, format="json")
    assert response.status_code == expected, response.data


def test_nobody_supervises_their_own_appraisal(cast, auth, cycle):
    """The whole process rests on there being a second person in it."""
    response = auth(cast["hr"]).post(APPRAISALS, {
        "cycle": str(cycle.id), "employee": str(cast["employee"].id),
        "supervisor": str(cast["employee"].id),
    }, format="json")
    assert response.status_code == 400
    assert "supervisor" in response.data


def test_nobody_sits_on_the_committee_reviewing_themselves(cast, auth, cycle):
    response = auth(cast["hr"]).post(APPRAISALS, {
        "cycle": str(cycle.id), "employee": str(cast["employee"].id),
        "supervisor": str(cast["supervisor"].id),
        "committee": [str(cast["employee"].id)],
    }, format="json")
    assert response.status_code == 400


@pytest.mark.parametrize("who,expected", [
    ("employee", 403), ("supervisor", 403), ("hr", 201),
])
def test_only_hr_manages_cycles(cast, auth, who, expected):
    response = auth(cast[who]).post(CYCLES, {
        "name": f"Cycle by {who}", "period_start": "2026-01-01",
        "period_end": "2026-12-31",
    }, format="json")
    assert response.status_code == expected


# ---------------------------------------------------------------------------
# Stage ownership — whose turn is it
# ---------------------------------------------------------------------------
def test_only_the_employee_writes_their_own_self_assessment(cast, auth, cycle,
                                                            make_appraisal):
    """A self-assessment somebody else can write is not one."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    for who in ("supervisor", "hr", "admin"):
        response = auth(cast[who]).patch(
            f"{APPRAISALS}{appraisal.id}/",
            {"self_assessment": "Written by somebody else."}, format="json")
        assert response.status_code == 403, who

    assert auth(cast["employee"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"self_assessment": "My own account of the year."},
        format="json").status_code == 200


def test_the_employee_cannot_write_the_supervisors_review(cast, auth, cycle,
                                                          make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SUPERVISOR_REVIEW)
    assert auth(cast["employee"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"supervisor_comments": "I reviewed myself favourably."},
        format="json").status_code == 403


def test_a_field_cannot_be_written_out_of_its_stage(cast, auth, cycle,
                                                    make_appraisal):
    """
    The record must show what each person said at the point they said it,
    unedited by the next in the chain.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    assert auth(cast["supervisor"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"supervisor_comments": "Getting ahead of myself."},
        format="json").status_code == 403


def test_a_supervisor_cannot_reach_into_the_self_assessment_in_a_mixed_patch(
        cast, auth, cycle, make_appraisal):
    """
    Guards are per FIELD, not per request: a supervisor may legitimately PATCH
    at their own stage, and must still not be able to rewrite the employee's
    words in the same call.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SUPERVISOR_REVIEW)
    response = auth(cast["supervisor"]).patch(f"{APPRAISALS}{appraisal.id}/", {
        "supervisor_comments": "A fair year.",
        "self_assessment": "Rewritten by the supervisor.",
    }, format="json")
    assert response.status_code == 403
    appraisal.refresh_from_db()
    assert "Rewritten" not in appraisal.self_assessment


def test_nobody_signs_off_their_own_appraisal(cast, auth, cycle,
                                              make_appraisal):
    """HR appraised by HR: even with org scope, the subject cannot finalise."""
    appraisal = make_appraisal(cycle, cast["hr"], cast["supervisor"],
                               status=Status.FINAL_REVIEW)
    assert auth(cast["hr"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"final_summary": "I rate myself highly."},
        format="json").status_code == 403


def test_only_hr_reassigns_who_reviews(cast, auth, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    assert auth(cast["supervisor"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"supervisor": str(cast["peer"].id)}, format="json").status_code == 403
    assert auth(cast["hr"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"supervisor": str(cast["peer"].id)}, format="json").status_code == 200


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------
def test_goals_are_fixed_once_agreed(cast, auth, cycle, make_appraisal):
    """Editing them retrospectively changes the question the person was asked."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    goal = appraisal.goals.first()
    assert auth(cast["supervisor"]).patch(
        f"{APPRAISALS}{appraisal.id}/goals/{goal.id}/",
        {"weight": 90}, format="json").status_code == 403


def test_the_employee_still_records_their_achievement_after_goals_are_fixed(
        cast, auth, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    goal = appraisal.goals.first()
    response = auth(cast["employee"]).patch(
        f"{APPRAISALS}{appraisal.id}/goals/{goal.id}/",
        {"achievement": "Delivered in full, with the report attached.",
         "progress_percent": 100}, format="json")
    assert response.status_code == 200
    assert response.data["progress_percent"] == 100


def test_an_outsider_cannot_add_a_goal(cast, auth, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    assert auth(cast["outsider"]).post(
        f"{APPRAISALS}{appraisal.id}/goals/",
        {"objective": "Injected objective", "weight": 10},
        format="json").status_code == 404


# ---------------------------------------------------------------------------
# Deletion and reopening
# ---------------------------------------------------------------------------
def test_an_appraisal_with_content_is_never_deleted(cast, auth, cycle,
                                                    make_appraisal):
    """Once there is a self-assessment in it, it is somebody's record."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SUPERVISOR_REVIEW)
    assert auth(cast["hr"]).delete(
        f"{APPRAISALS}{appraisal.id}/").status_code == 403


def test_an_untouched_appraisal_can_be_removed_by_hr(cast, auth, cycle,
                                                     make_appraisal):
    """One raised against the wrong person, before anybody wrote anything."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    assert auth(cast["hr"]).delete(
        f"{APPRAISALS}{appraisal.id}/").status_code == 204


@pytest.mark.parametrize("who,expected", [
    ("supervisor", 403), ("employee", 403), ("hr", 200), ("admin", 200),
])
def test_only_hr_reopens_a_closed_appraisal(cast, auth, cycle, make_appraisal,
                                            who, expected):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               [cast["committee"]], status=Status.CLOSED)
    response = auth(cast[who]).post(
        f"{APPRAISALS}{appraisal.id}/reopen/",
        {"remarks": "A factual correction is required here."}, format="json")
    assert response.status_code == expected, who
