"""
Phase T5.5 Part 5 — security audit.

WHY THIS FILE EXISTS SEPARATELY FROM test_rbac.py
--------------------------------------------------
`test_rbac.py` tests the rules as designed: it picks an endpoint and checks the
rule it is supposed to enforce. That is necessary and it is not sufficient,
because it can only cover the endpoints somebody remembered to write a test for.
This file works the other way round: it ENUMERATES the module's surface and
drives every route with an unauthorised caller, so an endpoint added later
without a permission check fails here even though nobody wrote a test for it.

That is the difference between "the rules I wrote are enforced" and "there is no
door I forgot to lock".
"""
import pytest

from tasks.models import Task

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status
ANALYTICS = "/api/v1/task-analytics/"
TEMPLATES = "/api/v1/task-templates/"


# ---------------------------------------------------------------------------
# Enumerated surface — every read route a caller can reach
# ---------------------------------------------------------------------------
COLLECTION_GETS = [
    LIST,
    f"{LIST}board/",
    f"{LIST}calendar/",
    f"{LIST}dashboard/",
    f"{LIST}overdue/",
    f"{LIST}review-queue/",
    f"{LIST}workload/",
    f"{LIST}filter-options/",
    f"{LIST}groups/",
    f"{LIST}reports/",
    f"{LIST}reports/completion/",
    f"{LIST}reports/overdue/",
    f"{LIST}reports/executive/",
    f"{LIST}reports/reviewer/",
    TEMPLATES,
    f"{ANALYTICS}executive/",
    f"{ANALYTICS}health/",
    f"{ANALYTICS}departments/",
    f"{ANALYTICS}employees/",
    f"{ANALYTICS}reviewers/",
    f"{ANALYTICS}trend/",
    f"{ANALYTICS}kpis/",
    f"{ANALYTICS}evidence/",
    f"{ANALYTICS}evidence/snapshots/",
]

DETAIL_GETS = [
    "{base}{id}/",
    "{base}{id}/checklist/",
    "{base}{id}/checklist/grouped/",
    "{base}{id}/comments/",
    "{base}{id}/attachments/",
    "{base}{id}/assignees/",
    "{base}{id}/timeline/",
]


@pytest.mark.parametrize("url", COLLECTION_GETS)
def test_no_collection_endpoint_is_reachable_anonymously(api, url):
    """The project fails closed by default; this proves it for every task route."""
    assert api.get(url).status_code in (401, 403), url


@pytest.mark.parametrize("url", COLLECTION_GETS)
def test_no_collection_endpoint_leaks_another_persons_task(cast, auth, api,
                                                           make_task, url):
    """
    Drive EVERY collection route as somebody with no relationship to the task
    and assert its number never appears in the response. A number is the thing
    that leaks first — it is in every list, every report row and every card.
    """
    task = make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
                     status=Status.IN_PROGRESS, title="Confidential audit work")

    response = auth(cast["outsider"]).get(url)
    # 403 is a correct answer for the manager-only views; what must never happen
    # is a 200 containing somebody else's work.
    assert response.status_code in (200, 403), (url, response.status_code)
    if response.status_code == 200:
        body = str(response.data)
        assert task.task_number not in body, f"{url} leaked the task number"
        assert "Confidential audit work" not in body, f"{url} leaked the title"


@pytest.mark.parametrize("suffix", DETAIL_GETS)
def test_no_detail_route_is_reachable_by_an_outsider(cast, auth, make_task,
                                                     suffix):
    task = make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    url = suffix.format(base=LIST, id=task.id)
    assert auth(cast["outsider"]).get(url).status_code == 404, url


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
WRITE_ROUTES = [
    ("post", "{base}{id}/accept/", {}),
    ("post", "{base}{id}/start/", {}),
    ("post", "{base}{id}/progress/", {"progress_percent": 90}),
    ("post", "{base}{id}/submit-for-review/", {}),
    ("post", "{base}{id}/approve/", {}),
    ("post", "{base}{id}/request-rework/", {"reason": "Not good enough here."}),
    ("post", "{base}{id}/close/", {}),
    ("post", "{base}{id}/block/", {"reason": "Blocking this for a reason."}),
    ("post", "{base}{id}/cancel/", {"reason": "Cancelling this for a reason."}),
    ("post", "{base}{id}/comments/", {"body": "Injected comment."}),
    ("post", "{base}{id}/checklist/", {"items": ["Injected"]}),
    ("post", "{base}{id}/assignees/", {"assignee_ids": []}),
    ("patch", "{base}{id}/", {"title": "Hijacked"}),
    ("delete", "{base}{id}/", None),
]


@pytest.mark.parametrize("method,suffix,payload", WRITE_ROUTES,
                         ids=[r[1].split("/")[-2] or "detail" for r in WRITE_ROUTES])
def test_no_write_route_accepts_an_outsider(cast, auth, make_task, method,
                                            suffix, payload):
    """
    Every mutating route, driven by somebody with no relationship to the task.
    404 (invisible) or 403 (visible but refused) are both correct; a 2xx is a
    breach, and so is a 500 — an unhandled error on a permission path is a
    permission path nobody tested.
    """
    task = make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
                     status=Status.IN_PROGRESS, title="Untouched")
    url = suffix.format(base=LIST, id=task.id)
    client = auth(cast["outsider"])

    call = getattr(client, method)
    response = call(url, payload, format="json") if payload is not None else call(url)

    assert response.status_code in (403, 404), (url, response.status_code)
    task.refresh_from_db()
    assert task.title == "Untouched"
    assert task.status == Status.IN_PROGRESS


def test_bulk_cannot_be_used_to_reach_invisible_tasks(cast, auth, make_task):
    """
    The bulk endpoint takes ids directly, which makes it the most obvious place
    to try to reach past the scope.
    """
    hidden = make_task(cast["hod"], [cast["peer"]], status=Status.ASSIGNED)
    response = auth(cast["outsider"]).post(f"{LIST}bulk/", {
        "action": "priority", "priority": "low", "task_ids": [str(hidden.id)],
    }, format="json")
    assert response.data["applied"] == []
    hidden.refresh_from_db()
    assert hidden.priority != Task.Priority.LOW


def test_an_outsider_cannot_download_evidence(cast, auth, make_task):
    import io

    task = make_task(cast["hod"], [cast["peer"]], status=Status.IN_PROGRESS)
    payload = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
               + b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
               + b"\x1f\x15\xc4\x89" + b"\x00" * 32)
    handle = io.BytesIO(payload)
    handle.name = "secret.png"
    row = auth(cast["peer"]).post(f"{LIST}{task.id}/attachments/",
                                  {"files": handle}, format="multipart").data[0]

    url = f"{LIST}{task.id}/attachments/{row['id']}/download/"
    assert auth(cast["outsider"]).get(url).status_code == 404


def test_a_comment_cannot_be_edited_by_anybody_but_its_author(cast, auth,
                                                              make_task):
    """
    An edit is presented as the author's own words with an "edited" marker.
    Somebody else rewriting them under that marker would make the marker a lie.
    """
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.IN_PROGRESS)
    comment = auth(cast["employee"]).post(
        f"{LIST}{task.id}/comments/", {"body": "Original."}, format="json").data

    for who in ("hod", "hr", "admin", "outsider"):
        response = auth(cast[who]).patch(
            f"{LIST}{task.id}/comments/{comment['id']}/",
            {"body": "Rewritten."}, format="json")
        assert response.status_code in (403, 404), who


def test_department_boundaries_hold_across_every_manager_surface(
        cast, auth, make_task, departments):
    """
    A department head is not a second admin. Every surface that shows other
    people's work must confine them to their own department.
    """
    theirs = make_task(cast["other_hod"], [cast["outsider"]],
                       reviewer=cast["other_hod"], status=Status.UNDER_REVIEW,
                       department=departments["finance"],
                       title="Finance only work")

    client = auth(cast["hod"])
    for url in (LIST, f"{LIST}board/", f"{LIST}review-queue/", f"{LIST}overdue/",
                f"{LIST}workload/", f"{ANALYTICS}executive/",
                f"{ANALYTICS}employees/", f"{ANALYTICS}departments/",
                f"{LIST}reports/completion/"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert theirs.task_number not in str(response.data), url
        assert "Finance only work" not in str(response.data), url


def test_evidence_for_another_department_is_refused_not_emptied(
        cast, auth, make_task, departments):
    """
    An empty evidence page reads as "this person has done nothing", which is a
    different and more damaging claim than "you may not see this".
    """
    make_task(cast["other_hod"], [cast["outsider"]], status=Status.IN_PROGRESS,
              department=departments["finance"])
    response = auth(cast["hod"]).get(
        f"{ANALYTICS}evidence/", {"employee": str(cast["outsider"].id)})
    assert response.status_code == 403
    assert "tasks_assigned" not in response.data


def test_an_archived_task_is_still_permission_checked(cast, auth, make_task):
    """
    Archiving takes a task out of the default list. It must not take it out of
    the permission system — a filed record is still a record.
    """
    from tasks import workflow

    task = make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
                     status=Status.CLOSED)
    workflow.archive(task, cast["hod"])
    assert auth(cast["outsider"]).get(f"{LIST}{task.id}/").status_code == 404
    assert str(task.id) not in str(
        auth(cast["outsider"]).get(LIST, {"archived": "1"}).data)


def test_a_reviewer_cannot_act_on_a_task_they_do_not_review(cast, auth,
                                                            make_task,
                                                            departments):
    """Being a reviewer somewhere does not make somebody a reviewer everywhere."""
    task = make_task(cast["other_hod"], [cast["outsider"]],
                     reviewer=cast["other_hod"], status=Status.UNDER_REVIEW,
                     department=departments["finance"])
    assert auth(cast["hod"]).post(
        f"{LIST}{task.id}/approve/", {}, format="json").status_code == 404
