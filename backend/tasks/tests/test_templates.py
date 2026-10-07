"""
Phase T2.9 — task templates.

The design decision under test is that a template is a COPY SOURCE, not a living
parent. If editing "HR Onboarding" in March rewrote the checklist of every
onboarding in flight, it would change what people had already been asked to do
and invalidate ticks they had already made.
"""
import datetime

import pytest

from tasks.models import Task, TaskTemplate

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status
TEMPLATES = "/api/v1/task-templates/"


def seed(auth, user, name="Monthly Report", **extra):
    payload = {
        "name": name,
        "description": "The standing monthly return.",
        "priority": "high",
        "default_due_in_days": 14,
        "groups": [
            {"title": "Preparation", "items": ["Collect Data", "Draft Report"]},
            {"title": "Sign-off", "items": ["Review", "Final Submission"]},
        ],
        **extra,
    }
    return auth(user).post(TEMPLATES, payload, format="json")


# ---------------------------------------------------------------------------
# Creating and reading
# ---------------------------------------------------------------------------
def test_a_template_carries_a_grouped_checklist(cast, auth):
    response = seed(auth, cast["hod"])
    assert response.status_code == 201, response.data
    assert response.data["item_count"] == 4
    assert [g["title"] for g in response.data["groups"]] == ["Preparation",
                                                             "Sign-off"]


def test_template_names_are_unique(cast, auth):
    seed(auth, cast["hod"])
    clash = seed(auth, cast["hr"])
    assert clash.status_code == 400
    assert "name" in clash.data


@pytest.mark.parametrize("who,expected", [
    ("employee", 403), ("hod", 201), ("hr", 201), ("admin", 201),
])
def test_only_managers_may_create_a_template(cast, auth, who, expected):
    """A template is a shape somebody else will be held to."""
    assert seed(auth, cast[who], name=f"Shape by {who}").status_code == expected


def test_every_authenticated_user_may_read_the_catalogue(cast, auth):
    """
    An employee cannot create a task, but they can be shown what a "Monthly
    Report" involves. Hiding the catalogue buys nothing.
    """
    seed(auth, cast["hod"])
    response = auth(cast["employee"]).get(TEMPLATES)
    assert response.status_code == 200
    rows = response.data.get("results", response.data)
    assert [r["name"] for r in rows] == ["Monthly Report"]


def test_a_retired_template_leaves_the_picker_but_keeps_its_name(cast, auth):
    template_id = seed(auth, cast["hod"]).data["id"]
    auth(cast["hod"]).delete(f"{TEMPLATES}{template_id}/")

    listed = auth(cast["hod"]).get(TEMPLATES).data
    assert (listed.get("results", listed)) == []
    assert TaskTemplate.objects.get(pk=template_id).is_active is False

    included = auth(cast["hod"]).get(TEMPLATES, {"include_inactive": "1"}).data
    assert len(included.get("results", included)) == 1


def test_an_employee_cannot_retire_a_template(cast, auth):
    template_id = seed(auth, cast["hod"]).data["id"]
    assert auth(cast["employee"]).delete(
        f"{TEMPLATES}{template_id}/").status_code == 403


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------
def test_applying_a_template_copies_its_checklist(cast, auth, make_task):
    template_id = seed(auth, cast["hod"]).data["id"]
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)

    response = auth(cast["hod"]).post(f"{LIST}{task.id}/apply-template/",
                                      {"template": template_id}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["checklist_total"] == 4
    assert [g["title"] for g in response.data["checklist_groups"]] == [
        "Preparation", "Sign-off"]
    assert response.data["template_name"] == "Monthly Report"


def test_editing_a_template_never_touches_tasks_already_raised_from_it(
        cast, auth, make_task):
    """The whole point of copying rather than linking."""
    template_id = seed(auth, cast["hod"]).data["id"]
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(f"{LIST}{task.id}/apply-template/",
                           {"template": template_id}, format="json")

    auth(cast["hod"]).patch(f"{TEMPLATES}{template_id}/", {
        "name": "Monthly Report", "items": ["Something else entirely"],
        "groups": [],
    }, format="json")

    rows = auth(cast["hod"]).get(f"{LIST}{task.id}/checklist/").data
    assert len(rows) == 4
    assert "Something else entirely" not in [r["text"] for r in rows]


def test_applying_over_an_existing_checklist_is_refused(cast, auth, make_task):
    """
    Merging silently produces duplicates; replacing silently discards ticks. The
    caller clears the checklist first if that is what they meant.
    """
    template_id = seed(auth, cast["hod"]).data["id"]
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(f"{LIST}{task.id}/checklist/",
                           {"items": ["Already here"]}, format="json")

    response = auth(cast["hod"]).post(f"{LIST}{task.id}/apply-template/",
                                      {"template": template_id}, format="json")
    assert response.status_code == 400
    assert "workflow" in response.data


def test_a_template_cannot_be_applied_once_the_work_is_under_review(
        cast, auth, make_task):
    template_id = seed(auth, cast["hod"]).data["id"]
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    response = auth(cast["hod"]).post(f"{LIST}{task.id}/apply-template/",
                                      {"template": template_id}, format="json")
    assert response.status_code == 400


def test_applying_counts_a_use(cast, auth, make_task):
    template_id = seed(auth, cast["hod"]).data["id"]
    for _ in range(3):
        task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
        auth(cast["hod"]).post(f"{LIST}{task.id}/apply-template/",
                               {"template": template_id}, format="json")
    assert TaskTemplate.objects.get(pk=template_id).usage_count == 3


def test_applying_is_recorded_on_the_timeline(cast, auth, make_task):
    template_id = seed(auth, cast["hod"]).data["id"]
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(f"{LIST}{task.id}/apply-template/",
                           {"template": template_id}, format="json")
    actions = [r["action"] for r in
               auth(cast["hod"]).get(f"{LIST}{task.id}/timeline/").data]
    assert "template_applied" in actions


# ---------------------------------------------------------------------------
# Save as template
# ---------------------------------------------------------------------------
def test_a_task_can_be_frozen_as_a_template(cast, auth, make_task, tomorrow):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=tomorrow)
    auth(cast["hod"]).post(f"{LIST}{task.id}/checklist/", {
        "items": ["Kick-off"],
        "groups": [{"title": "Drafting", "items": ["Write", "Edit"]}],
    }, format="json")

    response = auth(cast["hod"]).post(f"{LIST}{task.id}/save-as-template/", {
        "name": "Website Launch", "default_due_in_days": 30,
    }, format="json")

    assert response.status_code == 201, response.data
    assert response.data["item_count"] == 3
    assert response.data["groups"][0]["title"] == "Drafting"
    assert [i["text"] for i in response.data["items"]] == ["Kick-off"]
    assert response.data["default_due_in_days"] == 30


def test_a_saved_template_carries_no_people_and_no_date(cast, auth, make_task,
                                                        tomorrow):
    """
    A template that assigned work to whoever did it last year would be worse
    than no template, and a date is stale the day after it is saved.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     due_date=tomorrow)
    auth(cast["hod"]).post(f"{LIST}{task.id}/save-as-template/",
                           {"name": "Audit Review"}, format="json")

    template = TaskTemplate.objects.get(name="Audit Review")
    assert template.default_due_in_days is None
    assert not hasattr(template, "assignees")
    # It does remember what to call the tasks raised from it.
    assert template.title_template == task.title


def test_saving_is_refused_for_an_employee(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    assert auth(cast["employee"]).post(
        f"{LIST}{task.id}/save-as-template/", {"name": "Mine"},
        format="json").status_code == 403


def test_saving_under_an_existing_name_is_refused(cast, auth, make_task):
    seed(auth, cast["hod"], name="HR Onboarding")
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    response = auth(cast["hod"]).post(f"{LIST}{task.id}/save-as-template/",
                                      {"name": "HR Onboarding"}, format="json")
    assert response.status_code == 400


def test_a_round_trip_preserves_the_shape(cast, auth, make_task):
    """Task -> template -> new task: the checklist comes out the way it went in."""
    source = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    auth(cast["hod"]).post(f"{LIST}{source.id}/checklist/", {
        "items": ["Kick-off"],
        "groups": [{"title": "Drafting", "items": ["Write", "Edit"]}],
    }, format="json")
    template_id = auth(cast["hod"]).post(
        f"{LIST}{source.id}/save-as-template/", {"name": "Round Trip"},
        format="json").data["id"]

    target = make_task(cast["hod"], [cast["peer"]], status=Status.ASSIGNED)
    body = auth(cast["hod"]).post(f"{LIST}{target.id}/apply-template/",
                                  {"template": template_id}, format="json").data

    assert body["checklist_total"] == 3
    assert [i["text"] for i in body["checklist"]] == ["Kick-off"]
    assert [i["text"] for i in body["checklist_groups"][0]["items"]] == [
        "Write", "Edit"]
