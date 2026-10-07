"""
Deep links (Phase TASK-DEEP-LINK-SHARING).

A task is addressable by its TASK NUMBER as well as its UUID, so the URL people
share is the reference they already say out loud.

THE WHOLE POINT OF THIS FILE IS THE SECOND HALF
-----------------------------------------------
A human-guessable URL is only safe if guessing it gets you nothing. Task numbers
run in sequence — anybody holding NIFN-TSK-2083-0004 can type 0005 — so these
tests walk the sequence as an outsider and require the same refusal the UUID
route gives. A link is a shortcut past SEARCHING, never past permission.
"""
import pytest

from tasks.models import Task
from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status


@pytest.fixture
def task(cast, make_task):
    return make_task(cast["hod"], [cast["employee"]], reviewer=cast["hr"],
                     status=Status.ASSIGNED)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def test_a_task_opens_by_its_number(task, cast, auth):
    res = auth(cast["employee"]).get(f"{LIST}{task.task_number}/")
    assert res.status_code == 200, res.data
    assert res.data["id"] == str(task.id)
    assert res.data["task_number"] == task.task_number


def test_the_uuid_route_still_works(task, cast, auth):
    """Every link already sent stays good."""
    res = auth(cast["employee"]).get(f"{LIST}{task.id}/")
    assert res.status_code == 200
    assert res.data["task_number"] == task.task_number


def test_the_number_is_case_insensitive(task, cast, auth):
    """A reference pasted out of an email arrives in whatever case it was typed."""
    res = auth(cast["employee"]).get(f"{LIST}{task.task_number.lower()}/")
    assert res.status_code == 200


def test_a_number_that_does_not_exist_is_a_clean_404(cast, auth):
    res = auth(cast["employee"]).get(f"{LIST}NIFN-TSK-2083-9999/")
    assert res.status_code == 404


def test_rubbish_in_the_url_does_not_crash(cast, auth):
    """A UUID field asked for 'not-a-task' used to raise, not refuse."""
    for junk in ["not-a-task", "../etc", "NIFN-TSK-'; DROP TABLE tasks;--"]:
        assert auth(cast["employee"]).get(f"{LIST}{junk}/").status_code == 404


# ---------------------------------------------------------------------------
# Security — the same answer as the UUID route, for every reader
# ---------------------------------------------------------------------------
def test_an_outsider_is_refused_the_number_exactly_as_the_uuid(task, cast, auth):
    outsider = auth(cast["outsider"])
    by_uuid = outsider.get(f"{LIST}{task.id}/")
    by_number = outsider.get(f"{LIST}{task.task_number}/")
    assert by_uuid.status_code == by_number.status_code == 404


def test_walking_the_sequence_finds_nothing(cast, auth, make_task):
    """
    Numbers are sequential, so a person holding one can type the next. Every
    task in the run belongs to somebody else; none of them may open.
    """
    numbers = [make_task(cast["hod"], [cast["employee"]], reviewer=cast["hr"],
                         status=Status.ASSIGNED).task_number
               for _ in range(3)]
    outsider = auth(cast["outsider"])
    assert [outsider.get(f"{LIST}{n}/").status_code for n in numbers] == [404] * 3


@pytest.mark.parametrize("who,allowed", [
    ("hod", True),        # the creator
    ("employee", True),   # the assignee
    ("hr", True),         # the chosen reviewer
    ("outsider", False),  # another department, no involvement
])
def test_the_link_opens_for_exactly_the_people_the_task_is_visible_to(
        task, cast, auth, who, allowed):
    res = auth(cast[who]).get(f"{LIST}{task.task_number}/")
    assert (res.status_code == 200) is allowed


def test_an_anonymous_visitor_gets_nothing(task, api):
    """No public links, no tokens, no guest access — the phase is explicit."""
    assert api.get(f"{LIST}{task.task_number}/").status_code in (401, 403)


def test_a_shared_link_carries_no_more_than_the_page_does(task, cast, auth):
    """
    Opening by number returns the SAME payload as opening by id — the preview a
    recipient sees is the detail serializer, with its capability flags computed
    for THEM. There is no second, looser representation for shared links.
    """
    body = auth(cast["employee"]).get(f"{LIST}{task.task_number}/").data
    for field in ("title", "task_number", "status", "workflow_label", "priority",
                  "assignees", "reviewer_name", "capabilities"):
        assert field in body
    # The assignee may accept; they may not review their own work.
    assert body["capabilities"]["can_accept"] is True
    assert body["capabilities"]["can_review"] is False


# ---------------------------------------------------------------------------
# Actions, not just reads
# ---------------------------------------------------------------------------
def test_a_transition_can_be_driven_through_the_number(task, cast, auth):
    """Everything under the detail route resolves the same way, so a person who
    followed a link does not hit a wall on the first button."""
    res = auth(cast["employee"]).post(f"{LIST}{task.task_number}/accept/")
    assert res.status_code == 200, res.data
    task.refresh_from_db()
    assert task.status == Status.ACCEPTED


def test_an_outsider_cannot_act_through_the_number_either(task, cast, auth):
    res = auth(cast["outsider"]).post(f"{LIST}{task.task_number}/accept/")
    assert res.status_code == 404
    task.refresh_from_db()
    assert task.status == Status.ASSIGNED
