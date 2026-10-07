"""
Subtasks, derived progress, the open-subtask gate, rich descriptions and the
edit conflict check (Phase TASK-AUTOSAVE-AND-SUBTASKS).
"""
import pytest

from notifications.models import Category, Notification
from tasks.models import Task, TaskSubtask

pytestmark = pytest.mark.django_db
Status = Task.Status


def subtasks_url(task):
    return f"/api/v1/tasks/{task.pk}/subtasks/"


def add(client, task, title, **extra):
    response = client.post(subtasks_url(task), {"title": title, **extra},
                           format="json")
    assert response.status_code == 201, response.data
    return response.data["subtask"]


def complete(client, task, sid, done=True):
    return client.post(f"{subtasks_url(task)}{sid}/complete/",
                       {"is_done": done}, format="json")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def test_owner_adds_subtasks_and_the_detail_carries_them(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    add(auth(cast["hod"]), task, "Configure Server")
    add(auth(cast["hod"]), task, "Deploy Backend",
        assignee=str(cast["employee"].pk))

    detail = auth(cast["hod"]).get(f"/api/v1/tasks/{task.pk}/").data
    assert [s["title"] for s in detail["subtasks"]] == ["Configure Server",
                                                        "Deploy Backend"]
    assert detail["subtask_total"] == 2
    assert detail["subtask_done"] == 0
    assert detail["subtask_percent"] == 0
    assert detail["subtasks"][1]["assignee_name"]
    assert detail["capabilities"]["can_manage_subtasks"] is True


def test_an_assignee_cannot_define_subtasks(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    response = auth(cast["employee"]).post(subtasks_url(task), {"title": "X"},
                                           format="json")
    assert response.status_code == 403


def test_a_subtask_can_only_go_to_somebody_on_the_task(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    response = auth(cast["hod"]).post(
        subtasks_url(task), {"title": "X", "assignee": str(cast["peer"].pk)},
        format="json")
    assert response.status_code == 400
    assert "assignee" in response.data


def test_assigning_notifies_the_assignee_once(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    add(auth(cast["hod"]), task, "Deploy", assignee=str(cast["employee"].pk))
    rows = Notification.objects.filter(recipient=cast["employee"],
                                       category=Category.TASK_SUBTASK_ASSIGNED)
    assert rows.count() == 1


def test_reorder_refuses_a_foreign_id(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    other = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    a = add(auth(cast["hod"]), task, "A")
    b = add(auth(cast["hod"]), task, "B")
    foreign = add(auth(cast["hod"]), other, "Elsewhere")

    bad = auth(cast["hod"]).post(f"{subtasks_url(task)}reorder/",
                                 {"ids": [b["id"], foreign["id"]]}, format="json")
    assert bad.status_code == 400

    good = auth(cast["hod"]).post(f"{subtasks_url(task)}reorder/",
                                  {"ids": [b["id"], a["id"]]}, format="json")
    assert [row["title"] for row in good.data] == ["B", "A"]


def test_deleting_a_subtask_takes_its_comments_with_it(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    row = add(auth(cast["hod"]), task, "Deploy")
    comment = auth(cast["employee"]).post(
        f"/api/v1/tasks/{task.pk}/comments/",
        {"body": "On it", "subtask": row["id"]}, format="json")
    assert comment.status_code == 201
    assert str(comment.data["subtask"]) == row["id"]

    assert auth(cast["hod"]).delete(
        f"{subtasks_url(task)}{row['id']}/").status_code == 204
    assert not task.comments.exists()
    assert not TaskSubtask.objects.filter(pk=row["id"]).exists()


def test_removing_an_assignee_from_the_task_unassigns_their_subtasks(
        cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"], cast["peer"]],
                     status=Status.IN_PROGRESS)
    row = add(auth(cast["hod"]), task, "Deploy", assignee=str(cast["peer"].pk))
    auth(cast["hod"]).post(f"/api/v1/tasks/{task.pk}/assignees/",
                           {"assignee_ids": [str(cast["employee"].pk)]},
                           format="json")
    assert TaskSubtask.objects.get(pk=row["id"]).assignee_id is None


# ---------------------------------------------------------------------------
# Progress precedence
# ---------------------------------------------------------------------------
def test_progress_derives_from_subtasks_and_overrides_a_hand_report(
        cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    auth(cast["employee"]).post(f"/api/v1/tasks/{task.pk}/progress/",
                                {"progress_percent": 60}, format="json")
    task.refresh_from_db()
    assert task.progress_percent == 60 and task.progress_is_auto is False

    ids = [add(auth(cast["hod"]), task, f"S{i}")["id"] for i in range(5)]
    task.refresh_from_db()
    assert task.progress_percent == 0 and task.progress_is_auto is True

    for sid in ids[:3]:
        response = complete(auth(cast["employee"]), task, sid)
        assert response.status_code == 200
    assert response.data["progress_percent"] == 60
    assert response.data["subtask_done"] == 3
    assert response.data["subtask_total"] == 5

    reopened = complete(auth(cast["employee"]), task, ids[0], done=False)
    assert reopened.data["progress_percent"] == 40


def test_manual_progress_is_refused_while_subtasks_exist(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    add(auth(cast["hod"]), task, "S")
    response = auth(cast["employee"]).post(
        f"/api/v1/tasks/{task.pk}/progress/", {"progress_percent": 50},
        format="json")
    assert response.status_code == 409


def test_subtasks_win_over_the_checklist(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    auth(cast["hod"]).post(f"/api/v1/tasks/{task.pk}/checklist/",
                           {"items": ["A", "B"]}, format="json")
    row = auth(cast["hod"]).get(f"/api/v1/tasks/{task.pk}/checklist/").data[0]
    auth(cast["employee"]).post(
        f"/api/v1/tasks/{task.pk}/checklist/{row['id']}/tick/",
        {"is_done": True}, format="json")
    task.refresh_from_db()
    assert task.progress_percent == 50

    add(auth(cast["hod"]), task, "S1")
    add(auth(cast["hod"]), task, "S2")
    add(auth(cast["hod"]), task, "S3")
    task.refresh_from_db()
    assert task.progress_percent == 0


def test_an_outsider_cannot_complete_a_subtask(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    row = add(auth(cast["hod"]), task, "S")
    assert complete(auth(cast["outsider"]), task, row["id"]).status_code == 404
    assert complete(auth(cast["peer"]), task, row["id"]).status_code in (403, 404)


# ---------------------------------------------------------------------------
# The gate: nothing is submitted or closed with an open subtask
# ---------------------------------------------------------------------------
def test_submit_for_review_waits_for_every_subtask(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    a = add(auth(cast["hod"]), task, "A")
    b = add(auth(cast["hod"]), task, "B")
    complete(auth(cast["employee"]), task, a["id"])

    detail = auth(cast["employee"]).get(f"/api/v1/tasks/{task.pk}/").data
    assert detail["capabilities"]["can_submit_for_review"] is False
    assert detail["capabilities"]["open_subtask_count"] == 1

    refused = auth(cast["employee"]).post(
        f"/api/v1/tasks/{task.pk}/submit-for-review/", {}, format="json")
    assert refused.status_code in (400, 403)
    assert "subtask" in str(refused.data).lower()

    complete(auth(cast["employee"]), task, b["id"])
    allowed = auth(cast["employee"]).post(
        f"/api/v1/tasks/{task.pk}/submit-for-review/", {}, format="json")
    assert allowed.status_code == 200
    assert allowed.data["status"] == Status.UNDER_REVIEW


def test_last_subtask_on_shared_work_tells_the_other_assignees(
        cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"], cast["peer"]],
                     status=Status.IN_PROGRESS)
    row = add(auth(cast["hod"]), task, "Only piece")
    complete(auth(cast["employee"]), task, row["id"])
    done = Notification.objects.filter(category=Category.TASK_SUBTASK_COMPLETED)
    assert {n.recipient_id for n in done} == {cast["hod"].pk, cast["peer"].pk}


# ---------------------------------------------------------------------------
# Rich description, create with subtasks, conflict check
# ---------------------------------------------------------------------------
def test_an_html_description_is_sanitized_and_summarised(cast, auth):
    response = auth(cast["hod"]).post("/api/v1/tasks/", {
        "title": "Website Deployment",
        "description": "<h2>Task Goal</h2><p>Ship it</p>"
                       "<img src=x onerror=alert(1)><script>bad()</script>",
        "description_format": "html",
        "reviewer": str(cast["hr"].pk),
        "assignee_ids": [str(cast["employee"].pk)],
        "subtasks": [{"title": "Configure Server"},
                     {"title": "Deploy Backend",
                      "assignee": str(cast["employee"].pk)}],
    }, format="json")
    assert response.status_code == 201, response.data
    assert "onerror" not in response.data["description"]
    assert "<script" not in response.data["description"]
    assert "<h2>Task Goal</h2>" in response.data["description"]
    assert response.data["description_format"] == "html"
    assert response.data["summary"].startswith("Task Goal Ship it")
    assert [s["title"] for s in response.data["subtasks"]] == [
        "Configure Server", "Deploy Backend"]
    assert response.data["subtasks"][1]["assignee_name"]


def test_an_old_task_keeps_its_plain_text(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    detail = auth(cast["hod"]).get(f"/api/v1/tasks/{task.pk}/").data
    assert detail["description_format"] == "text"


def test_a_stale_edit_is_refused_with_the_current_task(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    loaded = auth(cast["hod"]).get(f"/api/v1/tasks/{task.pk}/").data

    # Somebody else saves first.
    other = auth(cast["employee"]).patch(
        f"/api/v1/tasks/{task.pk}/", {"priority": "high"}, format="json")
    assert other.status_code == 200

    stale = auth(cast["hod"]).patch(
        f"/api/v1/tasks/{task.pk}/",
        {"title": "Renamed", "expected_updated_at": loaded["updated_at"]},
        format="json")
    assert stale.status_code == 409
    assert stale.data["task"]["priority"] == "high"
    task.refresh_from_db()
    assert task.title != "Renamed"

    fresh = auth(cast["hod"]).patch(
        f"/api/v1/tasks/{task.pk}/",
        {"title": "Renamed", "expected_updated_at": stale.data["task"]["updated_at"]},
        format="json")
    assert fresh.status_code == 200
