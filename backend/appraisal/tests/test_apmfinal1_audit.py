"""
Phase APM-FINAL.1 — production hardening audit.

Adversarial checks for Parts 5 and 6. Every one of these asks the question a
person would ask if they wanted to see somebody else's appraisal, or wanted a
figure in the record to say something it should not.

Most assert an ABSENCE. That is the point: a security audit that only proves the
right people get in has tested half of what matters.
"""
import pytest

from appraisal.models import Appraisal, EvidenceReference
from evidence.schema import Source
from .conftest import APPRAISALS, CYCLES

pytestmark = pytest.mark.django_db

Status = Appraisal.Status


# ===========================================================================
# PART 5 — Evidence audit
# ===========================================================================
def test_all_seven_declared_sources_are_reported_with_their_availability(
        cast, auth, cycle, make_appraisal):
    """
    The registry declares seven sources and implements one. All seven must come
    back, because "no Leave evidence" and "Leave evidence is not collected" are
    different claims and only one is true — and somebody appraised on task
    activity alone is entitled to know which.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast["employee"]).get(
        f"{APPRAISALS}{appraisal.id}/evidence/").data

    reported = {row["source"]: row["available"] for row in body["sources"]}
    assert set(reported) == {s.value for s in Source}
    assert reported["task"] is True
    for absent in ("memo", "minute", "circular", "attendance", "leave",
                   "inventory"):
        assert reported[absent] is False, absent


def test_the_headline_carries_every_figure_or_marks_it_missing(
        cast, auth, cycle, make_appraisal):
    """A panel that silently loses a row is how somebody is appraised against
    six figures believing they saw seven."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast["employee"]).get(
        f"{APPRAISALS}{appraisal.id}/evidence/").data

    keys = [m["key"] for m in body["headline"]]
    assert keys == ["tasks_assigned", "tasks_completed",
                    "task_completion_percent", "on_time_percent",
                    "review_participation", "evidence_uploaded",
                    "checklist_completion"]
    assert all("missing" in m for m in body["headline"])


def test_citing_the_same_evidence_twice_does_not_duplicate_it(
        cast, auth, cycle, make_appraisal):
    """
    Two rows of the same evidence captured minutes apart are noise in a record
    somebody may have to read years later — and a reader counting citations
    would conclude the figures had been checked twice.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    url = f"{APPRAISALS}{appraisal.id}/attach-evidence/"
    for _ in range(3):
        assert auth(cast["employee"]).post(
            url, {"note": "Same window."}, format="json").status_code == 200

    assert EvidenceReference.objects.filter(appraisal=appraisal).count() == 1


def test_evidence_cited_against_different_goals_is_not_deduplicated(
        cast, auth, cycle, make_appraisal):
    """The other half of the rule: two objectives may each cite the period, and
    collapsing those would lose which objective the figures were offered for."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    goals = list(appraisal.goals.all())
    url = f"{APPRAISALS}{appraisal.id}/attach-evidence/"
    for goal in goals:
        assert auth(cast["employee"]).post(
            url, {"goal": str(goal.id), "note": f"For {goal.objective}."},
            format="json").status_code == 200

    assert EvidenceReference.objects.filter(
        appraisal=appraisal).count() == len(goals)


def test_a_citation_is_frozen_and_does_not_follow_the_live_figures(
        cast, auth, cycle, make_appraisal):
    """
    The figure in the discussion and the figure in the record must still match
    months later. A citation that recomputed would rewrite what was said.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    auth(cast["employee"]).post(
        f"{APPRAISALS}{appraisal.id}/attach-evidence/", {}, format="json")
    reference = EvidenceReference.objects.get(appraisal=appraisal)
    before = reference.metrics

    # Whatever happens to the underlying tasks, the citation does not move.
    from django.apps import apps
    apps.get_model("tasks", "Task").objects.all().delete()
    reference.refresh_from_db()
    assert reference.metrics == before


def test_the_appraisal_module_recomputes_no_evidence_of_its_own():
    """
    A second evidence system is a second set of numbers that disagrees with the
    first, and the disagreement surfaces in a conversation about somebody's
    year. `evidence_link` reads the CONTRACT and aggregates nothing.
    """
    import inspect
    from appraisal import evidence_link

    source = inspect.getsource(evidence_link)
    for forbidden in ("aggregate(", "annotate(", "Count(", "Avg(", "Sum("):
        assert forbidden not in source, (
            f"appraisal.evidence_link contains {forbidden} — it must cite the "
            f"evidence contract, never compute a figure of its own.")


# ===========================================================================
# PART 6 — Security audit
# ===========================================================================
def test_an_outsider_sees_nothing_at_all(cast, auth, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    outsider = auth(cast["outsider"])

    assert outsider.get(f"{APPRAISALS}{appraisal.id}/").status_code == 404
    assert outsider.get(
        f"{APPRAISALS}{appraisal.id}/evidence/").status_code == 404
    assert outsider.get(
        f"{APPRAISALS}{appraisal.id}/timeline/").status_code == 404
    rows = outsider.get(APPRAISALS).data
    assert (rows["results"] if isinstance(rows, dict) else rows) == []


def test_a_department_head_is_not_entitled_to_their_departments_appraisals(
        cast, auth, cycle, make_appraisal):
    """
    THE boundary that separates this module from every other one. Membership is
    explicit — named supervisor, named committee — never inferred from an org
    chart. `cast["committee"]` is a CHECKER in the same department as the
    employee, and supervises nobody.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["peer"])
    same_department_head = cast["committee"]
    assert same_department_head.department_ref_id == \
        cast["employee"].department_ref_id
    assert same_department_head.role == "checker"

    assert auth(same_department_head).get(
        f"{APPRAISALS}{appraisal.id}/").status_code == 404


def test_a_supervisor_sees_their_reports_and_not_a_colleagues(
        cast, auth, cycle, make_appraisal):
    mine = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    theirs = make_appraisal(cycle, cast["peer"], cast["committee"])

    assert auth(cast["supervisor"]).get(
        f"{APPRAISALS}{mine.id}/").status_code == 200
    assert auth(cast["supervisor"]).get(
        f"{APPRAISALS}{theirs.id}/").status_code == 404


def test_review_ownership_cannot_be_borrowed_from_another_appraisal(
        cast, auth, cycle, make_appraisal):
    """
    Being a supervisor SOMEWHERE grants nothing anywhere else. The guards are
    per-appraisal, not per-role.
    """
    theirs = make_appraisal(cycle, cast["peer"], cast["committee"],
                            status=Status.SUPERVISOR_REVIEW)
    assert auth(cast["supervisor"]).post(
        f"{APPRAISALS}{theirs.id}/record-supervisor-review/").status_code == 404


def test_nobody_writes_into_a_stage_that_is_not_theirs(cast, auth, cycle,
                                                       make_appraisal):
    """Guarded per FIELD, not per request: a supervisor may legitimately PATCH
    at their own stage and must still not reach into the self-assessment."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SUPERVISOR_REVIEW)
    refusals = [
        (cast["supervisor"], {"self_assessment": "Not mine."}),
        (cast["employee"], {"supervisor_comments": "Not mine."}),
        (cast["employee"], {"final_summary": "Not mine."}),
        (cast["employee"], {"promotion_readiness": "ready"}),
        (cast["supervisor"], {"committee_comments": "Not mine."}),
    ]
    for actor, payload in refusals:
        response = auth(actor).patch(f"{APPRAISALS}{appraisal.id}/", payload,
                                     format="json")
        assert response.status_code == 403, (actor.username, payload)


def test_only_hr_may_change_who_reviews_an_appraisal(cast, auth, cycle,
                                                     make_appraisal):
    """Reassigning the reviewer is how somebody would route their own appraisal
    to a friendlier reader."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    assert auth(cast["employee"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"supervisor": str(cast["peer"].id)}, format="json").status_code == 403
    assert auth(cast["supervisor"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"committee": [str(cast["peer"].id)]},
        format="json").status_code == 403
    assert auth(cast["hr"]).patch(
        f"{APPRAISALS}{appraisal.id}/",
        {"supervisor": str(cast["peer"].id)}, format="json").status_code == 200


def test_evidence_access_follows_the_appraisal_not_the_person(
        cast, auth, cycle, make_appraisal):
    """
    The evidence pack is somebody's activity record. Reading it must require
    being able to read the APPRAISAL it belongs to — otherwise the endpoint is a
    way to read anybody's figures by knowing an id.
    """
    theirs = make_appraisal(cycle, cast["peer"], cast["committee"])
    for actor in ("employee", "supervisor", "outsider"):
        assert auth(cast[actor]).get(
            f"{APPRAISALS}{theirs.id}/evidence/").status_code == 404, actor
    assert auth(cast["hr"]).get(
        f"{APPRAISALS}{theirs.id}/evidence/").status_code == 200


def test_a_closed_appraisal_is_immutable_for_everybody(cast, auth, cycle,
                                                       make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.CLOSED)
    for actor in ("employee", "supervisor", "hr", "admin"):
        assert auth(cast[actor]).patch(
            f"{APPRAISALS}{appraisal.id}/", {"final_summary": "Rewritten."},
            format="json").status_code == 403, actor
        assert auth(cast[actor]).post(
            f"{APPRAISALS}{appraisal.id}/attach-evidence/", {},
            format="json").status_code == 403, actor


def test_anybody_may_READ_a_cycle_but_only_hr_may_change_one(cast, auth):
    """
    Reading a cycle is open on purpose, and this test exists to say so.

    A cycle carries the round's name, its period and its three deadlines —
    which is exactly what an employee needs in order to know when their own
    self-assessment is expected. Gating it to HR would mean the people the
    deadlines apply to could not see them.

    Nothing personal lives on a cycle. The one figure that is not purely
    calendar is `appraisal_count`, an organisation-wide total with no names in
    it. WRITING remains HR-only, which is where the sensitivity actually is:
    opening, activating and closing a round.
    """
    for actor in ("employee", "supervisor", "committee", "hr"):
        assert auth(cast[actor]).get(CYCLES).status_code == 200, actor

    payload = {"name": "Unauthorised cycle", "period_start": "2026-01-01",
               "period_end": "2026-12-31"}
    for actor in ("employee", "supervisor", "committee"):
        assert auth(cast[actor]).post(
            CYCLES, payload, format="json").status_code == 403, actor
    assert auth(cast["hr"]).post(
        CYCLES, payload, format="json").status_code == 201


def test_a_cycle_exposes_no_personal_data_to_the_people_who_can_read_it(
        cast, auth, cycle, make_appraisal):
    """The read being open is only safe while a cycle stays impersonal."""
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    body = auth(cast["outsider"]).get(CYCLES).data
    rows = body["results"] if isinstance(body, dict) else body

    for row in rows:
        assert "appraisals" not in row
        for personal in ("employee", "employee_name", "supervisor",
                         "promotion_readiness", "self_assessment"):
            assert personal not in row, personal


def test_reports_can_never_contain_an_appraisal_the_caller_cannot_open(
        cast, auth, cycle, make_appraisal):
    """
    Reports carry no permission of their own — they run over the caller's
    VISIBLE set. That is only safe if the visible set is the same one the detail
    endpoint uses.
    """
    make_appraisal(cycle, cast["employee"], cast["supervisor"])
    make_appraisal(cycle, cast["peer"], cast["committee"])

    rows = auth(cast["supervisor"]).get(
        f"{APPRAISALS}reports/appraisal-summary/").data["rows"]
    names = {r["employee"] for r in rows}
    assert cast["employee"].get_full_name() in names
    assert cast["peer"].get_full_name() not in names

    assert auth(cast["outsider"]).get(
        f"{APPRAISALS}reports/appraisal-summary/").data["rows"] == []
