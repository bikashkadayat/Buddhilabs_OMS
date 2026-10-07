"""
Final security audit for the task module (Phase TASK-MODULE-FINAL-HARDENING).

Not new behaviour - the five claims the phase asks to VERIFY, each asserted
through the API rather than by calling a permission helper, because the API is
what an attacker reaches:

  1. An employee cannot approve a task they were not chosen to review.
  2. The person doing the work cannot review it - not even as an Admin.
  3. The chosen reviewer can.
  4. An Admin override works and leaves an audit row naming who did it.
  5. A direct API call cannot bypass the reviewer rules: not by omitting the
     field, not by editing it afterwards, and not by swapping the assignee list
     to make the reviewer the assignee.
"""
import pytest

from tasks.models import Task, TaskAuditLog
from users.models import User
from .conftest import LIST

pytestmark = pytest.mark.django_db
Status = Task.Status


def _submitted(cast, auth, *, assignee, reviewer):
    """A task in Submitted, built through the real API by the real people."""
    res = auth(cast["hod"]).post(LIST, {
        "title": "Rack the new switch",
        "assignee_ids": [str(assignee.id)],
        "reviewer": str(reviewer.id)}, format="json")
    assert res.status_code == 201, res.data
    task = Task.objects.get(pk=res.data["id"])
    assert auth(assignee).post(f"{LIST}{task.id}/start/").status_code == 200
    assert auth(assignee).post(f"{LIST}{task.id}/submit-for-review/").status_code == 200
    return task


# --------------------------------------------------------------------------- #
# 1-3. Who may approve
# --------------------------------------------------------------------------- #
def test_an_employee_cannot_approve_a_task_they_do_not_review(cast, auth):
    task = _submitted(cast, auth, assignee=cast["employee"], reviewer=cast["hr"])
    assert auth(cast["peer"]).post(f"{LIST}{task.id}/approve/").status_code in (403, 404)
    task.refresh_from_db()
    assert task.status == Status.UNDER_REVIEW


def test_a_department_head_cannot_approve_a_task_they_do_not_review(cast, auth):
    """No department-based approvals: heading the department is not authority."""
    task = _submitted(cast, auth, assignee=cast["employee"], reviewer=cast["hr"])
    assert auth(cast["hod"]).post(f"{LIST}{task.id}/approve/").status_code == 403


def test_the_person_doing_the_work_cannot_review_it(cast, auth):
    task = _submitted(cast, auth, assignee=cast["employee"], reviewer=cast["hr"])
    assert auth(cast["employee"]).post(f"{LIST}{task.id}/approve/").status_code == 403
    assert auth(cast["employee"]).post(
        f"{LIST}{task.id}/request-rework/", {"remarks": "Fine by me, honestly."},
        format="json").status_code == 403


def test_not_even_an_admin_may_approve_work_they_are_doing(cast, auth):
    """The override is for unsticking somebody else's review, not your own."""
    task = _submitted(cast, auth, assignee=cast["admin"], reviewer=cast["hr"])
    assert auth(cast["admin"]).post(f"{LIST}{task.id}/approve/").status_code == 403


def test_the_chosen_reviewer_approves(cast, auth):
    task = _submitted(cast, auth, assignee=cast["employee"], reviewer=cast["hr"])
    assert auth(cast["hr"]).post(f"{LIST}{task.id}/approve/").status_code == 200
    task.refresh_from_db()
    assert task.status == Status.COMPLETED


# --------------------------------------------------------------------------- #
# 4. The Admin override is audited
# --------------------------------------------------------------------------- #
def test_an_admin_override_is_recorded_against_the_admin(cast, auth):
    task = _submitted(cast, auth, assignee=cast["employee"], reviewer=cast["hr"])
    assert auth(cast["admin"]).post(f"{LIST}{task.id}/approve/").status_code == 200

    row = (TaskAuditLog.objects.filter(task=task,
                                       action=TaskAuditLog.Action.REVIEW_APPROVED)
           .latest("created_at"))
    assert row.actor == cast["admin"], "the trail names who stepped in"
    assert row.actor_name == cast["admin"].get_full_name()
    assert row.from_status == Status.UNDER_REVIEW
    assert row.to_status == Status.COMPLETED


# --------------------------------------------------------------------------- #
# 5. The API cannot be talked around
# --------------------------------------------------------------------------- #
def test_a_task_cannot_be_created_without_a_reviewer(cast, auth):
    res = auth(cast["hod"]).post(LIST, {
        "title": "No reviewer", "assignee_ids": [str(cast["employee"].id)]},
        format="json")
    assert res.status_code == 400, res.data


def test_the_reviewer_cannot_be_edited_into_being_the_assignee(cast, auth):
    """The rule holds on PATCH, not only on create."""
    res = auth(cast["hod"]).post(LIST, {
        "title": "Rack the new switch",
        "assignee_ids": [str(cast["employee"].id)],
        "reviewer": str(cast["hr"].id)}, format="json")
    task_id = res.data["id"]
    edit = auth(cast["hod"]).patch(f"{LIST}{task_id}/",
                                   {"reviewer": str(cast["employee"].id)},
                                   format="json")
    assert edit.status_code == 400, edit.data
    assert Task.objects.get(pk=task_id).reviewer == cast["hr"]


def test_the_assignee_list_cannot_be_swapped_to_include_the_reviewer(cast, auth):
    """
    The other direction: leave the reviewer alone and move the ASSIGNEE onto
    them. Refused by the same rule, which is why it is checked from both ends.
    """
    res = auth(cast["hod"]).post(LIST, {
        "title": "Rack the new switch",
        "assignee_ids": [str(cast["employee"].id)],
        "reviewer": str(cast["hr"].id)}, format="json")
    task_id = res.data["id"]
    edit = auth(cast["hod"]).patch(f"{LIST}{task_id}/",
                                   {"assignee_ids": [str(cast["hr"].id)]},
                                   format="json")
    assert edit.status_code == 400, edit.data
    assert [a.user for a in Task.objects.get(pk=task_id).assignees.all()] == [
        cast["employee"]]


def test_status_cannot_be_posted_to_skip_the_ladder(cast, auth):
    """tasks.workflow is the only thing that may assign to `status`."""
    res = auth(cast["hod"]).post(LIST, {
        "title": "Straight to closed", "status": "closed",
        "assignee_ids": [str(cast["employee"].id)],
        "reviewer": str(cast["hr"].id)}, format="json")
    assert res.status_code == 201
    assert res.data["status"] == Status.ASSIGNED


def test_a_task_cannot_be_approved_before_it_is_submitted(cast, auth):
    res = auth(cast["hod"]).post(LIST, {
        "title": "Approve me early",
        "assignee_ids": [str(cast["employee"].id)],
        "reviewer": str(cast["hr"].id)}, format="json")
    task_id = res.data["id"]
    assert auth(cast["hr"]).post(f"{LIST}{task_id}/approve/").status_code == 403


def test_an_outsider_cannot_even_see_the_task(cast, auth):
    """404 rather than 403: a 403 confirms the id exists."""
    task = _submitted(cast, auth, assignee=cast["employee"], reviewer=cast["hr"])
    assert auth(cast["outsider"]).get(f"{LIST}{task.id}/").status_code == 404
    assert auth(cast["outsider"]).post(f"{LIST}{task.id}/approve/").status_code == 404
