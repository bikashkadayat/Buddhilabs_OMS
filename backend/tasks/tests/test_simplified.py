"""
The simplified task (Phase TASK-SIMPLIFICATION).

Replaces test_asana_model.py, which pinned Personal / Department / Assigned and
the rules about which roles could raise which. All of that is gone. Three things
are left, and each is asserted through the real API:

  1. ONE TASK, ANYBODY MAY RAISE IT, FOR ANYBODY. The Employee who could not
     previously name somebody else now can, and so can the Board - which is the
     one governance line this phase deliberately moved (the Board still reads
     governance modules and cannot touch administrative configuration).

  2. THE CREATOR PICKS BOTH PEOPLE. When they do not name a reviewer, the server
     fills the blank with their department head, then with them, then with HR -
     always skipping anybody who is doing the work.

  3. NOBODY REVIEWS THEIR OWN WORK. Refused at creation by name, and refused
     again at decision time, because those are two different moments and a rule
     enforced at only one of them is a rule with a way around it.
"""
from datetime import date

import pytest

from tasks.models import Task
from users.models import User
from .conftest import LIST, make_user

pytestmark = pytest.mark.django_db
Status = Task.Status


@pytest.fixture
def board_member(db):
    return make_user("simp_board", User.Roles.BOD, "Board Member", department="")


def _create(auth, user, reviewer=None, **body):
    """
    Create a task. Both people are mandatory, so the helper fills them in - the
    creator does the work, a department head reviews it - and the tests that are
    ABOUT those rules pass their own values.
    """
    chosen = reviewer if reviewer is not None else _other_than(user)
    # Callers pass either a user or an id, whichever reads better where they are.
    payload = {"title": "Rack the new switch",
               "assignee_ids": [str(user.id)],
               "reviewer": str(getattr(chosen, "id", chosen))}
    payload.update(body)
    return auth(user).post(LIST, payload, format="json")


def _other_than(user):
    """Somebody active who is not `user` - a reviewer has to be somebody else."""
    return (User.objects.filter(is_active=True).exclude(pk=user.pk)
            .order_by("username").first())


# --------------------------------------------------------------------------- #
# 1. One task, anybody may raise it, for anybody
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("who", ["employee", "hod", "hr", "admin"])
def test_every_role_raises_a_task_the_same_way(cast, auth, who):
    res = _create(auth, cast[who])
    assert res.status_code == 201, res.data
    assert res.data["assignee_names"] == [cast[who].get_full_name()]


def test_a_task_must_say_who_is_doing_it(cast, auth):
    """
    Assigned To is mandatory. A task with nobody on it is a note, and this
    module exists to say who owes what - so the refusal names the fix rather
    than only the field.
    """
    res = auth(cast["employee"]).post(
        LIST, {"title": "Somebody should look at this",
               "reviewer": str(cast["hod"].id)}, format="json")
    assert res.status_code == 400, res.data
    assert "who is doing this task" in str(res.data["assignee_ids"])
    assert not Task.objects.filter(title="Somebody should look at this").exists()


def test_the_assignees_cannot_be_emptied_on_an_edit_either(cast, auth):
    """A task left with nobody on it is the same problem whenever it happens."""
    task_id = _create(auth, cast["hod"],
                      assignee_ids=[str(cast["employee"].id)]).data["id"]
    res = auth(cast["hod"]).patch(f"{LIST}{task_id}/", {"assignee_ids": []},
                                  format="json")
    assert res.status_code == 400, res.data
    assert Task.objects.get(pk=task_id).assignees.exists()


def test_an_employee_may_now_raise_work_for_somebody_else(cast, auth):
    """
    The rule that reversed. Until this phase an Employee naming anybody else was
    refused; the creator now chooses the assignee, whoever they are.
    """
    res = _create(auth, cast["employee"], title="Check the switch config",
                  assignee_ids=[str(cast["peer"].id)])
    assert res.status_code == 201, res.data
    assert res.data["assignee_names"] == [cast["peer"].get_full_name()]


def test_the_board_creates_tasks_like_anybody_else(auth, board_member, cast):
    """
    Deliberately moved this phase: "any authenticated user" now includes the
    Board. What did NOT move is the rest of its governance - it still reads
    governance modules and cannot reach administrative configuration, which
    users/test_bod_access.py holds down.
    """
    res = _create(auth, board_member, title="Board follow-up",
                  assignee_ids=[str(cast["employee"].id)])
    assert res.status_code == 201, res.data


def test_everybody_can_reach_the_person_search(cast, auth):
    """It is how a creator fills in either people field, so it cannot be gated."""
    for who in ("employee", "hod", "hr", "admin"):
        assert auth(cast[who]).get(f"{LIST}employees/").status_code == 200, who


def test_a_task_has_no_kind_to_choose_any_more(cast, auth):
    """
    The field is still on the row and on the payload so existing data and API
    consumers survive (the phase says preserve both), but it is inert: nothing
    asks for it and nothing branches on it.
    """
    res = _create(auth, cast["employee"])
    assert res.status_code == 201
    assert "task_type" in res.data, "kept for API compatibility"
    options = auth(cast["employee"]).get(f"{LIST}filter-options/").data
    assert "creatable_task_types" not in options
    assert "can_assign_to_others" not in options


# --------------------------------------------------------------------------- #
# 2. The creator picks both people
# --------------------------------------------------------------------------- #
def test_the_creator_chosen_reviewer_is_the_one_that_is_used(cast, auth):
    res = _create(auth, cast["employee"], reviewer=cast["hr"],
                  assignee_ids=[str(cast["peer"].id)])
    assert res.status_code == 201, res.data
    assert Task.objects.get(pk=res.data["id"]).reviewer == cast["hr"]


def test_a_task_cannot_be_raised_without_a_reviewer(cast, auth):
    """
    Phase TASK-REVIEWER-SELECTION. The server used to fill the blank itself -
    department head, then creator, then HR. A review the system routed is not a
    review the creator chose, so the blank is refused instead.
    """
    res = auth(cast["employee"]).post(
        LIST, {"title": "No reviewer", "assignee_ids": [str(cast["employee"].id)]},
        format="json")
    assert res.status_code == 400, res.data
    assert "required" in str(res.data["reviewer"]).lower()


def test_nothing_routes_the_review_any_more(cast, auth):
    """
    The department head is a SUGGESTION on the form and nothing else. Whoever
    the creator sent is who the task lands with, head or not.
    """
    res = _create(auth, cast["employee"], reviewer=cast["other_hod"])
    assert res.status_code == 201, res.data
    task = Task.objects.get(pk=res.data["id"])
    assert task.reviewer == cast["other_hod"]
    assert task.reviewer != cast["hod"], "the department's own head was not chosen"


def test_the_form_is_told_which_reviewer_to_suggest(cast, auth):
    options = auth(cast["employee"]).get(f"{LIST}filter-options/").data
    assert options["default_reviewer"]["id"] == str(cast["hod"].id)
    # A department head has no head above them, so there is nothing to suggest.
    assert auth(cast["hod"]).get(f"{LIST}filter-options/").data["default_reviewer"] is None


def test_a_department_with_no_head_still_blocks_nothing(cast, auth, departments):
    """
    The suggestion is absent, and the creator picks somebody. Task creation has
    not depended on department configuration since TASK-SIMPLIFICATION, and this
    phase did not put that back.
    """
    eng = departments["engineering"]
    eng.head = None
    eng.save(update_fields=["head"])
    assert auth(cast["employee"]).get(
        f"{LIST}filter-options/").data["default_reviewer"] is None
    assert _create(auth, cast["employee"], reviewer=cast["hr"]).status_code == 201


# --------------------------------------------------------------------------- #
# The brief's three examples, as written
# --------------------------------------------------------------------------- #
def test_creator_and_assignee_are_the_same_person_reviewed_by_the_head(cast, auth):
    """Creator: Bikash · Assigned To: Bikash · Reviewer: Department Head."""
    res = _create(auth, cast["employee"], reviewer=cast["hod"])
    task = Task.objects.get(pk=res.data["id"])
    _run_to_review(auth, cast["employee"], task)
    assert auth(cast["hr"]).post(f"{LIST}{task.id}/approve/").status_code == 403
    assert auth(cast["hod"]).post(f"{LIST}{task.id}/approve/").status_code == 200


def test_the_same_task_reviewed_by_hr_when_hr_is_who_was_chosen(cast, auth):
    """Creator: Bikash · Assigned To: Bikash · Reviewer: HR."""
    res = _create(auth, cast["employee"], reviewer=cast["hr"])
    task = Task.objects.get(pk=res.data["id"])
    _run_to_review(auth, cast["employee"], task)
    assert auth(cast["hod"]).post(f"{LIST}{task.id}/approve/").status_code == 403
    assert auth(cast["hr"]).post(f"{LIST}{task.id}/approve/").status_code == 200


def test_work_handed_to_somebody_else_is_reviewed_by_the_named_third_person(
        cast, auth):
    """Creator: Bikash · Assigned To: Prashanta · Reviewer: Sanjaya."""
    res = _create(auth, cast["hod"], assignee_ids=[str(cast["employee"].id)],
                  reviewer=cast["hr"])
    task = Task.objects.get(pk=res.data["id"])
    _run_to_review(auth, cast["employee"], task)
    # Not even the creator, unless they were the one chosen.
    assert auth(cast["hod"]).post(f"{LIST}{task.id}/approve/").status_code == 403
    assert auth(cast["hr"]).post(f"{LIST}{task.id}/approve/").status_code == 200


def _run_to_review(auth, worker, task):
    assert auth(worker).post(f"{LIST}{task.id}/start/").status_code == 200
    assert auth(worker).post(f"{LIST}{task.id}/submit-for-review/").status_code == 200


# --------------------------------------------------------------------------- #
# 3. Nobody reviews their own work
# --------------------------------------------------------------------------- #
def test_naming_the_assignee_as_reviewer_is_refused_at_creation(cast, auth):
    res = _create(auth, cast["hod"], assignee_ids=[str(cast["employee"].id)],
                  reviewer=str(cast["employee"].id))
    assert res.status_code == 400, res.data
    assert str(res.data["reviewer"][0]) == (
        "Reviewer cannot be the same as the assignee.")
    assert not Task.objects.filter(title="Rack the new switch").exists()


def test_it_is_refused_on_an_edit_too(cast, auth):
    res = _create(auth, cast["hod"], assignee_ids=[str(cast["employee"].id)])
    task_id = res.data["id"]
    edit = auth(cast["hod"]).patch(f"{LIST}{task_id}/",
                                   {"reviewer": str(cast["employee"].id)},
                                   format="json")
    assert edit.status_code == 400, edit.data


def test_the_assignee_still_cannot_approve_at_decision_time(cast, auth):
    """
    The same rule at the other end. Creation validation can be bypassed by an
    edit to the assignee list; this is what actually protects the decision.
    """
    res = _create(auth, cast["employee"], reviewer=str(cast["hod"].id))
    task = Task.objects.get(pk=res.data["id"])
    worker = auth(cast["employee"])
    worker.post(f"{LIST}{task.id}/start/")
    worker.post(f"{LIST}{task.id}/submit-for-review/")
    assert worker.post(f"{LIST}{task.id}/approve/").status_code == 403
    assert auth(cast["hod"]).post(f"{LIST}{task.id}/approve/").status_code == 200


# --------------------------------------------------------------------------- #
# The workflow: Created -> In Progress -> Submitted -> Approved -> Completed
# --------------------------------------------------------------------------- #
def test_work_starts_without_an_acceptance_step(cast, auth):
    """
    The five stages the phase names have no Accept in them. `accept` still
    exists for anybody who wants to acknowledge first - it just stopped being
    something everybody has to pass through.
    """
    res = _create(auth, cast["employee"], reviewer=str(cast["hod"].id))
    task = Task.objects.get(pk=res.data["id"])
    assert task.status == Status.ASSIGNED

    started = auth(cast["employee"]).post(f"{LIST}{task.id}/start/")
    assert started.status_code == 200, started.data
    task.refresh_from_db()
    assert task.status == Status.IN_PROGRESS
    # The acknowledgement stamp is filled rather than left blank, so the
    # timeline does not read as missing data.
    assert task.accepted_at is not None
    assert task.started_at is not None


def test_the_whole_ladder_runs_end_to_end(cast, auth):
    res = _create(auth, cast["employee"], reviewer=str(cast["hod"].id))
    task = Task.objects.get(pk=res.data["id"])
    # `auth` re-authenticates ONE shared client, so it is called at each step
    # rather than bound to two variables - two names for the same client would
    # both end up as whoever was authenticated last.
    assert auth(cast["employee"]).post(f"{LIST}{task.id}/start/").status_code == 200
    assert auth(cast["employee"]).post(
        f"{LIST}{task.id}/submit-for-review/").status_code == 200
    task.refresh_from_db()
    assert task.status == Status.UNDER_REVIEW

    assert auth(cast["hod"]).post(f"{LIST}{task.id}/approve/").status_code == 200
    task.refresh_from_db()
    assert task.status == Status.COMPLETED

    assert auth(cast["hod"]).post(f"{LIST}{task.id}/close/").status_code == 200
    task.refresh_from_db()
    assert task.status == Status.CLOSED


def test_the_one_step_personal_finish_is_gone(cast, auth):
    """Mark Done went with personal tasks; every task is reviewed now."""
    res = _create(auth, cast["employee"], reviewer=str(cast["hod"].id))
    task_id = res.data["id"]
    assert auth(cast["employee"]).post(f"{LIST}{task_id}/mark-done/").status_code == 404
    caps = auth(cast["employee"]).get(f"{LIST}{task_id}/").data["capabilities"]
    assert "can_mark_done" not in caps


# --------------------------------------------------------------------------- #
# Notifications reach the chosen reviewer
# --------------------------------------------------------------------------- #
def test_the_chosen_reviewer_is_told_when_work_is_submitted(
        cast, auth, django_capture_on_commit_callbacks):
    from notifications.models import Category, Notification

    res = _create(auth, cast["employee"], reviewer=cast["hr"])
    task = Task.objects.get(pk=res.data["id"])
    auth(cast["employee"]).post(f"{LIST}{task.id}/start/")
    with django_capture_on_commit_callbacks(execute=True):
        auth(cast["employee"]).post(f"{LIST}{task.id}/submit-for-review/")

    assert Notification.objects.filter(
        recipient=cast["hr"], category=Category.TASK_REVIEW_REQUIRED).exists()
    # Not the department head, who was never chosen.
    assert not Notification.objects.filter(
        recipient=cast["hod"], category=Category.TASK_REVIEW_REQUIRED).exists()


def test_an_admin_override_tells_the_reviewer_it_was_decided_without_them(
        cast, auth, django_capture_on_commit_callbacks):
    """
    The reviewer is not notified of their OWN decision - `_tell` never notifies
    the actor - but a decision taken over their head is exactly the case where
    silence would be wrong.
    """
    from notifications.models import Category, Notification

    res = _create(auth, cast["employee"], reviewer=cast["hr"])
    task = Task.objects.get(pk=res.data["id"])
    auth(cast["employee"]).post(f"{LIST}{task.id}/start/")
    auth(cast["employee"]).post(f"{LIST}{task.id}/submit-for-review/")
    with django_capture_on_commit_callbacks(execute=True):
        assert auth(cast["admin"]).post(f"{LIST}{task.id}/approve/").status_code == 200

    assert Notification.objects.filter(
        recipient=cast["hr"], category=Category.TASK_APPROVED).exists()
    assert Notification.objects.filter(
        recipient=cast["employee"], category=Category.TASK_APPROVED).exists()
