"""
The task API: creation, the detail page's sections, scopes and filters.

Asserted through HTTP rather than through the model layer, because the API is
the contract the frontend and any integration actually depend on.
"""
import datetime
import io

import pytest

from tasks.models import Task

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status


def ids(response):
    rows = response.data.get("results", response.data)
    return [row["id"] for row in rows]


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
def test_creating_a_task_with_assignees_assigns_it_in_one_request(
        cast, auth, tomorrow):
    """
    The create form has an assignee picker, so a task created with people on it
    is created ASSIGNED. Leaving it in Draft would mean every creation needed a
    second click to do the thing that was just asked for.
    """
    response = auth(cast["hod"]).post(LIST, {
        "title": "Prepare the quarterly return",
        "description": "Consolidated across all three cost centres.",
        "priority": "high",
        "due_date": str(tomorrow),
        "reviewer": str(cast["hod"].id),
        "assignee_ids": [str(cast["employee"].id), str(cast["peer"].id)],
        "checklist": ["Pull the ledger", "Reconcile", "File"],
    }, format="json")

    assert response.status_code == 201, response.data
    body = response.data
    assert body["status"] == Status.ASSIGNED
    assert body["task_number"].startswith("NIFN-TSK-")
    assert sorted(body["assignee_names"]) == sorted(
        [cast["employee"].get_full_name(), cast["peer"].get_full_name()])
    assert body["checklist_total"] == 3
    assert body["priority_label"] == "High"


def test_a_task_must_name_somebody_to_do_it(cast, auth):
    """
    Phase TASK-SIMPLIFICATION. It used to stay a Draft with nobody holding it.
    Assigned To is mandatory now, so a task without it is refused rather than
    parked in a state nobody is answerable for.
    """
    response = auth(cast["hod"]).post(
        LIST, {"title": "Something to work out later",
               "reviewer": str(cast["hr"].id)}, format="json")
    assert response.status_code == 400, response.data
    assert "who is doing this task" in str(response.data["assignee_ids"])


def test_a_task_must_name_somebody_to_review_it(cast, auth):
    """
    Phase TASK-REVIEWER-SELECTION. The server used to fill a missing reviewer in
    itself - department head, then creator, then HR. It no longer does: a review
    the system routed is not a review the creator chose.
    """
    response = auth(cast["hod"]).post(
        LIST, {"title": "Something to work out later",
               "assignee_ids": [str(cast["employee"].id)]}, format="json")
    assert response.status_code == 400, response.data
    assert "required" in str(response.data["reviewer"]).lower()


def test_the_department_is_inherited_from_the_assignee(cast, auth, departments):
    """
    A task belongs to the department it was raised for AT THE TIME. Deriving it
    at read time instead would silently move historical work when somebody
    transfers.
    """
    response = auth(cast["hr"]).post(LIST, {
        "title": "Reconcile the petty cash",
        "assignee_ids": [str(cast["outsider"].id)],
        "reviewer": str(cast["hod"].id),
    }, format="json")
    assert response.status_code == 201
    assert response.data["department_name"] == departments["finance"].name


def test_a_due_date_in_the_past_is_refused_on_creation(cast, auth, today):
    response = auth(cast["hod"]).post(LIST, {
        "title": "Something already late",
        "due_date": str(today - datetime.timedelta(days=1)),
    }, format="json")
    assert response.status_code == 400
    assert "due_date" in response.data


def test_an_overdue_task_can_still_be_edited(cast, auth, make_task, today):
    """
    Otherwise fixing a typo in the title of an overdue task would be blocked by
    its own due date, which is the wrong thing to enforce on the wrong field.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=today - datetime.timedelta(days=3))
    response = auth(cast["hod"]).patch(
        f"{LIST}{task.id}/", {"title": "Prepare the quarterly return (revised)"},
        format="json")
    assert response.status_code == 200, response.data
    assert response.data["is_overdue"] is True


def test_the_owner_can_extend_an_overdue_task_even_under_review(
        cast, auth, make_task, today):
    """
    A late task that has already been submitted for review is normally frozen
    (UNDER_REVIEW is not an editable stage). But an OVERDUE one must stay
    rescuable by its owner - extend the due date, fix the details - otherwise it
    strands where nobody can move it. The assignee still cannot.
    """
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW,
                     due_date=today - datetime.timedelta(days=2))

    # The owner extends the due date into the future — allowed because it is overdue.
    future = str(today + datetime.timedelta(days=7))
    ok = auth(cast["hod"]).patch(
        f"{LIST}{task.id}/", {"due_date": future}, format="json")
    assert ok.status_code == 200, ok.data
    assert ok.data["due_date"] == future

    # The assignee does not get the overdue rescue — review is the owner's call.
    denied = auth(cast["employee"]).patch(
        f"{LIST}{task.id}/", {"title": "nope"}, format="json")
    assert denied.status_code == 403


@pytest.mark.parametrize("title", ["", "ab", "   "])
def test_a_task_needs_a_real_title(cast, auth, title):
    response = auth(cast["hod"]).post(LIST, {"title": title}, format="json")
    assert response.status_code == 400


def test_status_cannot_be_set_through_the_write_serializer(cast, auth):
    """
    tasks.workflow is the only thing that may assign to `status`. A client that
    could post it would be able to close its own task and skip every guard.
    """
    response = auth(cast["hod"]).post(
        LIST, {"title": "Sneak past the engine", "status": "closed",
               "assignee_ids": [str(cast["employee"].id)],
               "reviewer": str(cast["hod"].id)},
        format="json")
    assert response.status_code == 201
    # ASSIGNED, not CLOSED: the posted status was ignored, and the task went
    # where a newly created task goes.
    assert response.data["status"] == Status.ASSIGNED


# ---------------------------------------------------------------------------
# The detail page's sections
# ---------------------------------------------------------------------------
def test_the_detail_payload_carries_every_section_the_page_renders(
        cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.IN_PROGRESS)
    body = auth(cast["employee"]).get(f"{LIST}{task.id}/").data
    for section in ("assignees", "checklist", "attachments", "comments",
                    "timeline", "progress_percent", "capabilities"):
        assert section in body, section


def test_the_checklist_can_be_ticked_and_reopened(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    auth(cast["hod"]).post(f"{LIST}{task.id}/checklist/",
                           {"items": ["Pull the ledger", "Reconcile"]},
                           format="json")
    item_id = auth(cast["employee"]).get(
        f"{LIST}{task.id}/checklist/").data[0]["id"]

    ticked = auth(cast["employee"]).post(
        f"{LIST}{task.id}/checklist/{item_id}/tick/", {"is_done": True},
        format="json")
    assert ticked.status_code == 200
    assert ticked.data["is_done"] is True
    assert ticked.data["done_by_name"] == cast["employee"].get_full_name()

    reopened = auth(cast["employee"]).post(
        f"{LIST}{task.id}/checklist/{item_id}/tick/", {"is_done": False},
        format="json")
    assert reopened.data["is_done"] is False
    assert reopened.data["done_at"] is None


def test_replacing_the_checklist_keeps_ticks_on_rows_that_survived(
        cast, auth, make_task):
    """
    Rebuilding from scratch would silently un-tick completed work every time
    somebody fixed a typo in another row.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    auth(cast["hod"]).post(f"{LIST}{task.id}/checklist/",
                           {"items": ["Pull the ledger", "Reconcile"]},
                           format="json")
    rows = auth(cast["hod"]).get(f"{LIST}{task.id}/checklist/").data
    auth(cast["employee"]).post(
        f"{LIST}{task.id}/checklist/{rows[0]['id']}/tick/", {"is_done": True},
        format="json")

    auth(cast["hod"]).post(
        f"{LIST}{task.id}/checklist/",
        {"items": ["Pull the ledger", "Reconcile ", "File"]}, format="json")
    after = {row["text"]: row["is_done"]
             for row in auth(cast["hod"]).get(f"{LIST}{task.id}/checklist/").data}
    assert after["Pull the ledger"] is True
    assert after["File"] is False


def test_anyone_who_can_read_a_live_task_can_comment_on_it(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    response = auth(cast["employee"]).post(
        f"{LIST}{task.id}/comments/", {"body": "The ledger export is missing May."},
        format="json")
    assert response.status_code == 201
    assert response.data["author_name"] == cast["employee"].get_full_name()
    assert auth(cast["outsider"]).post(
        f"{LIST}{task.id}/comments/", {"body": "Hello"},
        format="json").status_code == 404


def test_commenting_notifies_the_other_side_but_never_the_author(
        cast, auth, make_task):
    from notifications.models import Category, Notification

    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.IN_PROGRESS)
    auth(cast["employee"]).post(f"{LIST}{task.id}/comments/",
                                {"body": "A question about scope."}, format="json")
    recipients = set(Notification.objects.filter(
        category=Category.TASK_COMMENTED).values_list("recipient_id", flat=True))
    assert cast["hod"].id in recipients
    assert cast["employee"].id not in recipients


def test_an_empty_comment_is_refused(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    assert auth(cast["employee"]).post(
        f"{LIST}{task.id}/comments/", {"body": "   "},
        format="json").status_code == 400


def test_the_timeline_endpoint_returns_the_activity_in_order(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    rows = auth(cast["employee"]).get(f"{LIST}{task.id}/timeline/").data
    assert rows[0]["action"] == "created"
    assert rows[-1]["action"] == "submitted_for_review"
    assert all("action_label" in row for row in rows)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
def _png():
    """A real PNG, because the validator sniffs the leading bytes."""
    payload = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
               + b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
               + b"\x1f\x15\xc4\x89" + b"\x00" * 32)
    return io.BytesIO(payload)


def test_an_assignee_can_upload_evidence_and_read_it_back(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    upload = _png()
    upload.name = "evidence.png"
    response = auth(cast["employee"]).post(
        f"{LIST}{task.id}/attachments/",
        {"files": upload, "caption": "The filed return"}, format="multipart")
    assert response.status_code == 201, response.data
    assert response.data[0]["is_evidence"] is True
    assert response.data[0]["original_name"] == "evidence.png"
    # Never a public /media/ path — downloads go through the task's own gate.
    assert response.data[0]["download_url"].startswith(f"/api/v1/tasks/{task.id}/")


def test_a_disallowed_file_type_is_refused(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    bad = io.BytesIO(b"#!/bin/sh\nrm -rf /\n")
    bad.name = "payload.sh"
    response = auth(cast["employee"]).post(
        f"{LIST}{task.id}/attachments/", {"files": bad}, format="multipart")
    assert response.status_code == 400


def test_an_outsider_cannot_download_a_tasks_evidence(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    upload = _png()
    upload.name = "evidence.png"
    attachment_id = auth(cast["employee"]).post(
        f"{LIST}{task.id}/attachments/", {"files": upload},
        format="multipart").data[0]["id"]
    url = f"{LIST}{task.id}/attachments/{attachment_id}/download/"
    assert auth(cast["employee"]).get(url).status_code == 200
    assert auth(cast["outsider"]).get(url).status_code == 404


# ---------------------------------------------------------------------------
# Scopes — the module's menus
# ---------------------------------------------------------------------------
def test_my_tasks_is_live_work_assigned_to_me(cast, auth, make_task):
    mine = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    theirs = make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    closed = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                       status=Status.CLOSED)

    rows = ids(auth(cast["employee"]).get(LIST, {"scope": "mine"}))
    assert str(mine.id) in rows
    assert str(theirs.id) not in rows
    assert str(closed.id) not in rows


def test_assigned_by_me_is_what_i_raised(cast, auth, make_task):
    raised = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    other = make_task(cast["hr"], [cast["employee"]], status=Status.ASSIGNED)
    rows = ids(auth(cast["hod"]).get(LIST, {"scope": "assigned_by_me"}))
    assert str(raised.id) in rows and str(other.id) not in rows


def test_due_today_is_exactly_today(cast, auth, make_task, today, tomorrow):
    due = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                    due_date=today)
    later = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                      due_date=tomorrow)
    rows = ids(auth(cast["employee"]).get(LIST, {"scope": "due_today"}))
    assert str(due.id) in rows and str(later.id) not in rows


def test_overdue_excludes_work_that_is_already_finished(
        cast, auth, make_task, today):
    """
    A completed-but-not-yet-closed task is not overdue: the employee did their
    part, and chasing them for their reviewer's backlog is how a metric stops
    being believed.
    """
    late = today - datetime.timedelta(days=2)
    open_late = make_task(cast["hod"], [cast["employee"]],
                          status=Status.IN_PROGRESS, due_date=late)
    done_late = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                          status=Status.COMPLETED, due_date=late)

    rows = ids(auth(cast["employee"]).get(LIST, {"scope": "overdue"}))
    assert str(open_late.id) in rows
    assert str(done_late.id) not in rows


def test_team_tasks_is_empty_for_an_employee_rather_than_showing_their_own(
        cast, auth, make_task):
    """
    A menu that shows your own work under somebody else's label is a lie about
    what you are looking at.
    """
    make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    assert ids(auth(cast["employee"]).get(LIST, {"scope": "team"})) == []


def test_team_tasks_for_a_department_head_is_their_department(
        cast, auth, make_task, departments):
    ours = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     department=departments["engineering"])
    theirs = make_task(cast["other_hod"], [cast["outsider"]],
                       status=Status.ASSIGNED, department=departments["finance"])
    rows = ids(auth(cast["hod"]).get(LIST, {"scope": "team"}))
    assert str(ours.id) in rows and str(theirs.id) not in rows


def test_needs_me_covers_both_sides_of_the_workflow(cast, auth, make_task):
    to_accept = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    to_review = make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
                          status=Status.UNDER_REVIEW)

    assert str(to_accept.id) in ids(
        auth(cast["employee"]).get(LIST, {"scope": "needs_me"}))
    assert str(to_review.id) in ids(
        auth(cast["hod"]).get(LIST, {"scope": "needs_me"}))


def test_an_unknown_scope_falls_back_to_everything_visible(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    assert str(task.id) in ids(
        auth(cast["employee"]).get(LIST, {"scope": "nonsense"}))


# ---------------------------------------------------------------------------
# Filters and search
# ---------------------------------------------------------------------------
def test_the_status_filter_actually_filters(cast, auth, make_task):
    """
    The memo module shipped filter backends before it had any filterset fields,
    so `?status=draft` was accepted and ignored — a 200 with the wrong rows.
    """
    assigned = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    in_progress = make_task(cast["hod"], [cast["employee"]],
                            status=Status.IN_PROGRESS)
    rows = ids(auth(cast["hod"]).get(LIST, {"status": "assigned"}))
    assert rows == [str(assigned.id)]
    assert str(in_progress.id) not in rows


def test_several_statuses_can_be_filtered_at_once(cast, auth, make_task):
    a = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    b = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
              status=Status.CLOSED)
    rows = set(ids(auth(cast["hod"]).get(
        LIST, {"status_in": "assigned,in_progress"})))
    assert rows == {str(a.id), str(b.id)}


def test_search_matches_the_task_number_and_the_title(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     title="Audit the vehicle log")
    assert str(task.id) in ids(auth(cast["hod"]).get(LIST, {"search": "vehicle"}))
    assert str(task.id) in ids(
        auth(cast["hod"]).get(LIST, {"search": task.task_number}))


def test_the_list_can_be_ordered_by_due_date(cast, auth, make_task, today):
    late = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=today + datetime.timedelta(days=9))
    soon = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=today + datetime.timedelta(days=1))
    rows = ids(auth(cast["hod"]).get(LIST, {"ordering": "due_date"}))
    assert rows.index(str(soon.id)) < rows.index(str(late.id))


# ---------------------------------------------------------------------------
# Employee picker
# ---------------------------------------------------------------------------
def test_the_employee_picker_searches_by_name(cast, auth):
    """The specification is explicit: not assigned by department alone."""
    rows = auth(cast["hod"]).get(f"{LIST}employees/",
                                 {"search": "tsk employee"}).data
    assert any(row["full_name"] == cast["employee"].get_full_name() for row in rows)
    assert all({"id", "full_name", "designation", "department"} <= set(row)
               for row in rows)


def test_a_failed_creation_leaves_no_orphan_task_behind(cast, auth):
    """
    Creation is several writes that only make sense together. A duplicate
    assignee fails the request; it must not leave a task row nobody asked for,
    holding a number the sequence has already handed out and will never reissue.
    """
    before = Task.objects.count()
    duplicate = str(cast["employee"].id)
    response = auth(cast["hod"]).post(LIST, {
        "title": "Prepare the quarterly return",
        "assignee_ids": [duplicate, duplicate],
    }, format="json")
    assert response.status_code == 400, response.data
    assert Task.objects.count() == before
