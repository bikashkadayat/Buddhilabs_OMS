"""Task drafts follow task permissions (Phase TASK-AUTOSAVE-AND-SUBTASKS)."""
import pytest

from tasks.models import Task
from tasks.tests.conftest import cast, departments, make_task  # noqa: F401 - fixtures

pytestmark = pytest.mark.django_db


def url(key):
    return f"/api/v1/drafts/task/{key}/"


def test_anybody_may_keep_a_create_draft(api, cast):
    api.force_authenticate(cast["employee"])
    assert api.put(url("new"), {"payload": {"title": "Half"}},
                   format="json").status_code == 200
    assert api.get(url("new")).data["draft"]["payload"]["title"] == "Half"
    listed = api.get("/api/v1/drafts/").data
    assert listed[0]["kind"] == "task" and listed[0]["title"] == "Half"


def test_an_edit_draft_needs_edit_rights_on_the_task(api, cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]],
                     status=Task.Status.IN_PROGRESS)
    api.force_authenticate(cast["employee"])
    assert api.put(url(task.pk), {"payload": {"title": "x"}},
                   format="json").status_code == 200

    api.force_authenticate(cast["outsider"])
    assert api.put(url(task.pk), {"payload": {"title": "x"}},
                   format="json").status_code == 403
    assert api.get(url(task.pk)).status_code == 403


def test_a_submitted_task_cannot_be_drafted_against(api, cast, make_task):
    task = make_task(cast["hod"], [cast["employee"]],
                     status=Task.Status.UNDER_REVIEW)
    api.force_authenticate(cast["employee"])
    assert api.put(url(task.pk), {"payload": {}},
                   format="json").status_code == 403


def test_a_garbage_key_is_refused_not_crashed(api, cast):
    api.force_authenticate(cast["employee"])
    assert api.put(url("not-a-uuid"), {"payload": {}},
                   format="json").status_code == 403
