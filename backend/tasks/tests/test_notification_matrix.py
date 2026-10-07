"""
Notification audit (Phase TASK-MODULE-FINAL-HARDENING).

The six events the phase names, each fired through the real workflow and then
read back out of the Notification table - who was told, and who deliberately
was not.

THE ACTOR IS NEVER NOTIFIED OF THEIR OWN ACTION (tasks.receivers._tell). That is
why "Approved" does not reach the reviewer who just approved: they know. It
reaches them when somebody else decided - an Admin override - which is the case
where silence would be wrong.
"""
import pytest

from notifications.models import Category, Notification
from tasks.models import Task
from .conftest import LIST

pytestmark = pytest.mark.django_db


def _who(category):
    return set(Notification.objects.filter(category=category)
               .values_list("recipient__username", flat=True))


@pytest.fixture
def task(cast, auth, django_capture_on_commit_callbacks):
    """Created by the head, done by the employee, reviewed by HR."""
    with django_capture_on_commit_callbacks(execute=True):
        res = auth(cast["hod"]).post(LIST, {
            "title": "Rack the new switch",
            "assignee_ids": [str(cast["employee"].id)],
            "reviewer": str(cast["hr"].id)}, format="json")
    assert res.status_code == 201, res.data
    return Task.objects.get(pk=res.data["id"])


def test_task_created_tells_the_reviewer_not_the_creator(task, cast):
    """The person who raised it knows; the person who will answer for it may not."""
    told = _who(Category.TASK_CREATED)
    assert cast["hr"].username in told
    assert cast["hod"].username not in told


def test_task_assigned_tells_the_assignee(task, cast):
    told = _who(Category.TASK_ASSIGNED)
    assert cast["employee"].username in told
    assert cast["hod"].username not in told


def test_submitted_for_review_tells_the_chosen_reviewer_only(
        task, cast, auth, django_capture_on_commit_callbacks):
    auth(cast["employee"]).post(f"{LIST}{task.id}/start/")
    with django_capture_on_commit_callbacks(execute=True):
        auth(cast["employee"]).post(f"{LIST}{task.id}/submit-for-review/")
    told = _who(Category.TASK_REVIEW_REQUIRED)
    assert told == {cast["hr"].username}, told


def test_returned_tells_the_assignee(task, cast, auth,
                                     django_capture_on_commit_callbacks):
    auth(cast["employee"]).post(f"{LIST}{task.id}/start/")
    auth(cast["employee"]).post(f"{LIST}{task.id}/submit-for-review/")
    with django_capture_on_commit_callbacks(execute=True):
        assert auth(cast["hr"]).post(
            f"{LIST}{task.id}/request-rework/",
            {"reason": "The serial numbers are missing."},
            format="json").status_code == 200
    told = _who(Category.TASK_REWORK_REQUESTED)
    assert cast["employee"].username in told
    # Not the reviewer who sent it back: they know.
    assert cast["hr"].username not in told


def test_approved_tells_the_assignee(task, cast, auth,
                                     django_capture_on_commit_callbacks):
    auth(cast["employee"]).post(f"{LIST}{task.id}/start/")
    auth(cast["employee"]).post(f"{LIST}{task.id}/submit-for-review/")
    with django_capture_on_commit_callbacks(execute=True):
        assert auth(cast["hr"]).post(f"{LIST}{task.id}/approve/").status_code == 200
    told = _who(Category.TASK_APPROVED)
    assert cast["employee"].username in told
    assert cast["hr"].username not in told


def test_an_admin_override_reaches_the_reviewer_who_was_bypassed(
        task, cast, auth, django_capture_on_commit_callbacks):
    auth(cast["employee"]).post(f"{LIST}{task.id}/start/")
    auth(cast["employee"]).post(f"{LIST}{task.id}/submit-for-review/")
    with django_capture_on_commit_callbacks(execute=True):
        assert auth(cast["admin"]).post(f"{LIST}{task.id}/approve/").status_code == 200
    told = _who(Category.TASK_APPROVED)
    assert cast["hr"].username in told, "decided over their head, so they are told"
    assert cast["employee"].username in told


def test_completed_tells_the_assignee_when_it_is_closed(
        task, cast, auth, django_capture_on_commit_callbacks):
    auth(cast["employee"]).post(f"{LIST}{task.id}/start/")
    auth(cast["employee"]).post(f"{LIST}{task.id}/submit-for-review/")
    auth(cast["hr"]).post(f"{LIST}{task.id}/approve/")
    with django_capture_on_commit_callbacks(execute=True):
        assert auth(cast["hr"]).post(f"{LIST}{task.id}/close/").status_code == 200
    told = _who(Category.TASK_CLOSED)
    assert cast["employee"].username in told


def test_every_event_in_the_ladder_fires_exactly_once(
        task, cast, auth, django_capture_on_commit_callbacks):
    """
    A duplicate notification is as damaging as a missing one: it is what teaches
    people to stop reading them. Counted per category, end to end.
    """
    with django_capture_on_commit_callbacks(execute=True):
        auth(cast["employee"]).post(f"{LIST}{task.id}/start/")
        auth(cast["employee"]).post(f"{LIST}{task.id}/submit-for-review/")
    with django_capture_on_commit_callbacks(execute=True):
        auth(cast["hr"]).post(f"{LIST}{task.id}/approve/")
    with django_capture_on_commit_callbacks(execute=True):
        auth(cast["hr"]).post(f"{LIST}{task.id}/close/")

    for category in (Category.TASK_CREATED, Category.TASK_ASSIGNED,
                     Category.TASK_REVIEW_REQUIRED, Category.TASK_APPROVED,
                     Category.TASK_CLOSED):
        rows = Notification.objects.filter(category=category)
        by_person = {}
        for row in rows:
            by_person[row.recipient_id] = by_person.get(row.recipient_id, 0) + 1
        assert all(n == 1 for n in by_person.values()), f"{category}: {by_person}"
