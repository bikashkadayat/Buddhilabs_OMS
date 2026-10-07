"""
Task governance (Phase TASK-GOVERNANCE-HARDENING).

Three claims, each of which fails in a different direction if it is wrong:

  1. DEPARTMENT WORK ENDS AT ITS DEPARTMENT HEAD, with no fallback. The old HR
     fallback made "who reviews this department's output" depend on
     configuration nobody was looking at. A department with no head now refuses
     departmental work AT CREATION, saying so.

  2. A DEPENDENCY IS A GATE, NOT A NOTE. It is enforced in the workflow engine,
     so a direct POST is refused exactly as the hidden button would be - and it
     names what is in the way, because "blocked" with no name leaves somebody
     clicking the same button again.

  3. NO CYCLES, EVER. Refused when the edge is added, not detected afterwards:
     a ring of tasks each waiting for the next is a deadlock nobody can see and
     only an Admin could break.
"""
import pytest
from django.apps import apps

from tasks import workflow
from tasks import permissions as perms
from tasks.models import Task, TaskDependency
from tasks.services import (
    blocking_dependencies, department_head, departments_without_a_head,
    would_cycle,
)
from users.models import User
from .conftest import LIST, make_user

pytestmark = pytest.mark.django_db
Kind = Task.Kind
Status = Task.Status


@pytest.fixture
def headed(cast, departments):
    """Engineering with its head recorded - which the shared fixture already does."""
    return departments["engineering"]


@pytest.fixture
def headless(cast, departments):
    """
    Engineering with NOBODY recorded as its head.

    The shared `cast` fixture records one, because every other test in the suite
    needs a working department. These tests are about the case it does not
    cover, so the head is cleared here rather than by building a second cast.
    """
    eng = departments["engineering"]
    eng.head = None
    eng.save(update_fields=["head"])
    return eng


def _create(auth, user, **body):
    """Both people are mandatory, so the helper fills them in."""
    from users.models import User as _User

    reviewer = (_User.objects.filter(is_active=True).exclude(pk=user.pk)
                .order_by("username").first())
    payload = {"title": "Departmental work", "assignee_ids": [str(user.id)],
               "reviewer": str(reviewer.id)}
    payload.update(body)
    return auth(user).post(LIST, payload, format="json")


def _task(creator, assignees, status=Status.ACCEPTED, **kwargs):
    from .conftest import build_task

    return build_task(creator, assignees, status=status, **kwargs)


# --------------------------------------------------------------------------- #
# 1. The department head, after TASK-SIMPLIFICATION
#
# The three task kinds are gone, and with them the rule that departmental work
# HAD to be reviewed by a department head - and the refusal when no head was
# recorded. What survives is the DEFAULT: the head is who the server fills in
# when the creator names nobody, and the creator may always override it.
# --------------------------------------------------------------------------- #
def test_the_head_is_a_suggestion_on_the_form_and_nothing_more(cast, auth, headed):
    """
    Phase TASK-REVIEWER-SELECTION moved the default off the server entirely: it
    is what the create form pre-fills, and the creator may send anybody. Nothing
    fills a missing reviewer in afterwards, because the field is now required.
    """
    options = auth(cast["employee"]).get(f"{LIST}filter-options/").data
    assert options["default_reviewer"]["id"] == str(cast["hod"].id)

    # And the creator's own choice is what the task gets.
    res = _create(auth, cast["employee"], reviewer=str(cast["hr"].id))
    assert res.status_code == 201, res.data
    assert Task.objects.get(pk=res.data["id"]).reviewer == cast["hr"]


def test_a_department_with_no_head_no_longer_refuses_work(cast, auth, headless):
    """
    This is the reversal. Work in an unowned department used to be refused
    outright; the creator now chooses the reviewer, so a missing head costs a
    SUGGESTION on the form and nothing else.
    """
    assert auth(cast["employee"]).get(
        f"{LIST}filter-options/").data["default_reviewer"] is None
    res = _create(auth, cast["employee"], reviewer=str(cast["hr"].id))
    assert res.status_code == 201, res.data
    task = Task.objects.get(pk=res.data["id"])
    assert task.reviewer == cast["hr"]
    assert not perms.is_assignee(task.reviewer, task)


def test_the_creator_can_always_choose_somebody_other_than_the_head(cast, auth,
                                                                    headed):
    res = _create(auth, cast["employee"], reviewer=str(cast["other_hod"].id))
    assert res.status_code == 201, res.data
    assert Task.objects.get(pk=res.data["id"]).reviewer == cast["other_hod"]


def test_an_inactive_head_is_not_suggested(cast, auth, headed):
    cast["hod"].is_active = False
    cast["hod"].save(update_fields=["is_active"])
    assert department_head(headed.pk) is None
    assert auth(cast["employee"]).get(
        f"{LIST}filter-options/").data["default_reviewer"] is None


def test_the_departments_missing_a_head_are_still_listed_for_whoever_can_fix_it(
        cast, headless, departments):
    """
    The governance surfaces (System Health, the dashboard banner, the ownership
    report) are unchanged by this phase - a department with nobody answerable is
    still worth naming, it just no longer blocks a task.
    """
    missing = departments_without_a_head()
    assert "Engineering" in missing
    assert "Finance" not in missing


# --------------------------------------------------------------------------- #
# 2. Dependencies are a gate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", [TaskDependency.Kind.BLOCKED_BY,
                                  TaskDependency.Kind.DEPENDS_ON])
def test_a_task_cannot_move_forward_while_its_prerequisite_is_unfinished(
        cast, auth, kind):
    """
    Both wordings are the same gate. They differ in what they tell a reader, not
    in what they enforce - a "soft" dependency that turned out to be hard, or
    the reverse, is exactly the surprise this asserts against.
    """
    prerequisite = _task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    task = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    workflow.add_dependency(task, prerequisite, cast["hod"], kind=kind)

    worker = auth(cast["employee"])
    refused = worker.post(f"{LIST}{task.id}/start/")
    assert refused.status_code == 400, refused.data
    # It NAMES what is in the way, and what state that is in.
    assert prerequisite.task_number in str(refused.data["workflow"])
    assert "In Progress" in str(refused.data["workflow"])
    task.refresh_from_db()
    assert task.status == Status.ACCEPTED


def test_the_gate_lifts_when_the_prerequisite_finishes(cast, auth):
    prerequisite = _task(cast["hod"], [cast["peer"]], status=Status.UNDER_REVIEW)
    task = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    workflow.add_dependency(task, prerequisite, cast["hod"])
    assert auth(cast["employee"]).post(f"{LIST}{task.id}/start/").status_code == 400

    workflow.approve_review(prerequisite, cast["hod"])
    assert not blocking_dependencies(task)
    assert auth(cast["employee"]).post(f"{LIST}{task.id}/start/").status_code == 200


def test_a_cancelled_prerequisite_does_not_freeze_a_task_forever(cast, auth):
    """
    It is never going to finish. A dependency that outlived the work it pointed
    at would need an Admin to clear, and the person blocked cannot see why.
    """
    prerequisite = _task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    task = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    workflow.add_dependency(task, prerequisite, cast["hod"])
    workflow.cancel(prerequisite, cast["hod"], "Dropped from the quarter's plan.")

    assert not blocking_dependencies(task)
    assert auth(cast["employee"]).post(f"{LIST}{task.id}/start/").status_code == 200


def test_submitting_is_gated_too_not_just_starting(cast, auth):
    """Otherwise the gate is a speed bump: start it yesterday, finish it anyway."""
    prerequisite = _task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    task = _task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    workflow.add_dependency(task, prerequisite, cast["hod"])
    refused = auth(cast["employee"]).post(f"{LIST}{task.id}/submit-for-review/")
    assert refused.status_code == 400, refused.data
    assert prerequisite.task_number in str(refused.data["workflow"])


def test_the_engine_refuses_a_blocked_move_even_when_the_api_is_bypassed(cast):
    """The gate lives in the workflow engine, so there is no way around it."""
    from rest_framework.exceptions import ValidationError

    prerequisite = _task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    task = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    workflow.add_dependency(task, prerequisite, cast["hod"])
    with pytest.raises(ValidationError):
        workflow.start(task, cast["employee"])


def test_an_assignee_may_record_what_their_task_is_waiting_on(cast, auth):
    """
    They are the person who discovers it. A dependency only a manager could
    record is one that gets recorded in a comment instead.
    """
    # A prerequisite this employee can actually see: they are on it too. Work
    # they cannot read is refused, and has its own test below.
    prerequisite = _task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    task = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    res = auth(cast["employee"]).post(
        f"{LIST}{task.id}/dependencies/",
        {"depends_on": str(prerequisite.id), "kind": "blocked_by"}, format="json")
    assert res.status_code == 201, res.data
    assert res.data["dependencies"][0]["depends_on_number"] == prerequisite.task_number

    # And may remove it again.
    row_id = res.data["dependencies"][0]["id"]
    assert auth(cast["employee"]).delete(
        f"{LIST}{task.id}/dependencies/{row_id}/").status_code == 200
    assert not TaskDependency.objects.filter(pk=row_id).exists()


def test_somebody_outside_the_task_cannot_change_what_it_waits_for(cast, auth):
    prerequisite = _task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    task = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    res = auth(cast["outsider"]).post(
        f"{LIST}{task.id}/dependencies/",
        {"depends_on": str(prerequisite.id)}, format="json")
    assert res.status_code in (403, 404), res.data


def test_a_task_cannot_be_made_to_wait_for_work_the_asker_cannot_see(cast, auth):
    """Otherwise the block itself tells them a task they may not read exists."""
    secret = _task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
                   department=None)
    task = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    res = auth(cast["employee"]).post(
        f"{LIST}{task.id}/dependencies/", {"depends_on": str(secret.id)},
        format="json")
    assert res.status_code == 403, res.data


# --------------------------------------------------------------------------- #
# 3. No cycles
# --------------------------------------------------------------------------- #
def test_two_tasks_cannot_wait_for_each_other(cast, auth):
    first = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    second = _task(cast["hod"], [cast["peer"]], status=Status.ACCEPTED)
    workflow.add_dependency(first, second, cast["hod"])

    assert would_cycle(second, first)
    res = auth(cast["hod"]).post(f"{LIST}{second.id}/dependencies/",
                                 {"depends_on": str(first.id)}, format="json")
    assert res.status_code == 400, res.data
    assert "wait for each other" in str(res.data)


def test_a_longer_ring_is_refused_too(cast, auth):
    """A -> B -> C, then C -> A. The two-step check would miss this one."""
    a = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    b = _task(cast["hod"], [cast["peer"]], status=Status.ACCEPTED)
    c = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    workflow.add_dependency(a, b, cast["hod"])
    workflow.add_dependency(b, c, cast["hod"])
    assert would_cycle(c, a)
    res = auth(cast["hod"]).post(f"{LIST}{c.id}/dependencies/",
                                 {"depends_on": str(a.id)}, format="json")
    assert res.status_code == 400, res.data


def test_a_task_cannot_wait_for_itself(cast, auth):
    task = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    res = auth(cast["hod"]).post(f"{LIST}{task.id}/dependencies/",
                                 {"depends_on": str(task.id)}, format="json")
    assert res.status_code == 400, res.data


def test_waiting_for_finished_work_is_refused_as_pointless(cast, auth):
    """An edge that can never block is an edge that tells its reader a lie."""
    done = _task(cast["hod"], [cast["peer"]], status=Status.COMPLETED)
    task = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    res = auth(cast["hod"]).post(f"{LIST}{task.id}/dependencies/",
                                 {"depends_on": str(done.id)}, format="json")
    assert res.status_code == 400, res.data


# --------------------------------------------------------------------------- #
# The board, the milestones, the payload
# --------------------------------------------------------------------------- #
def test_the_department_board_is_the_same_four_columns_as_every_other_board(
        cast, auth):
    """
    It used to be the five-column board minus Backlog. Backlog is gone for
    everybody (TASK-SIMPLIFICATION), so the variant is kept only so an existing
    caller's `?variant=department` stays a valid request.
    """
    body = auth(cast["hod"]).get(f"{LIST}board/?variant=department").data
    assert [c["key"] for c in body["columns"]] == ["todo", "in_progress", "review",
                                                   "done"]
    full = auth(cast["hod"]).get(f"{LIST}board/").data
    assert [c["key"] for c in full["columns"]] == [c["key"] for c in body["columns"]]


def test_a_draft_sits_in_to_do_rather_than_falling_off_the_board(cast, auth):
    """
    Nothing may disappear because a column was removed. A draft is To Do: it has
    been written and not started, which is what that column means.
    """
    _task(cast["hod"], [cast["employee"]], status=Status.DRAFT)
    body = auth(cast["hod"]).get(f"{LIST}board/?variant=department").data
    assert body["total"] == sum(c["count"] for c in body["columns"])
    assert Status.DRAFT in body["columns"][0]["statuses"]


def test_a_card_says_how_much_work_it_is_waiting_on(cast, auth):
    prerequisite = _task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    task = _task(cast["hod"], [cast["employee"]], status=Status.ACCEPTED)
    workflow.add_dependency(task, prerequisite, cast["hod"])
    rows = auth(cast["hod"]).get(f"{LIST}?scope=all").data["results"]
    card = next(r for r in rows if r["id"] == str(task.id))
    assert card["blocked_by_count"] == 1
    other = next(r for r in rows if r["id"] == str(prerequisite.id))
    assert other["blocked_by_count"] == 0


def test_the_task_page_carries_the_four_milestones_in_order(cast, auth):
    task = _task(cast["hod"], [cast["employee"]], status=Status.COMPLETED)
    body = auth(cast["hod"]).get(f"{LIST}{task.id}/").data
    assert [m["key"] for m in body["milestones"]] == [
        "created", "started", "reviewed", "completed"]
    assert all(m["at"] for m in body["milestones"]), "a finished task reached them all"


def test_a_milestone_not_reached_is_empty_rather_than_guessed(cast, auth):
    task = _task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    milestones = {m["key"]: m["at"]
                  for m in auth(cast["hod"]).get(f"{LIST}{task.id}/").data["milestones"]}
    assert milestones["created"]
    assert milestones["started"] is None
    assert milestones["reviewed"] is None
    assert milestones["completed"] is None


def test_the_department_report_names_the_head_or_says_there_is_none(cast, auth,
                                                                    departments):
    """
    Phase DEPARTMENT-GOVERNANCE-HARDENING, Impact Review: a department
    performance report that does not say who is accountable for the numbers is
    a report nobody can act on - and a department with nobody is the finding,
    not a blank cell.
    """
    from tasks import analytics as task_analytics

    orphan = departments["finance"]
    orphan.head = None
    orphan.save(update_fields=["head"])
    _task(cast["other_hod"], [cast["outsider"]], status=Status.ASSIGNED,
          department=orphan)
    _task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
          department=departments["engineering"])

    payload = task_analytics.REPORTS["department-performance"]["build"](
        Task.objects.all())
    assert any(c["key"] == "head" for c in payload["columns"])
    rows = {row["department"]: row for row in payload["rows"]}
    assert rows[orphan.name]["head"] == "NO HEAD"
    assert rows[departments["engineering"].name]["head"] == cast["hod"].get_full_name()
    # Named in the summary too, so somebody reading only the headline still
    # learns that part of this report has no owner.
    assert payload["summary"]["departments_without_a_head"] == 1
