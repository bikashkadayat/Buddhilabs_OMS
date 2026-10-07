"""
Shared fixtures for the task suite.

Every test that needs a task in flight goes through these, so there is exactly
one definition of "a task submitted for review". The memo suite learned this the
hard way: each test built its routing by hand, and when the engine changed they
all broke at once.
"""
import datetime

import pytest
from django.apps import apps
from rest_framework.test import APIClient

from tasks import workflow
from tasks.models import Task
from tasks.services import generate_task_number
from users.models import User

LIST = "/api/v1/tasks/"


@pytest.fixture
def api():
    return APIClient()


def make_user(username, role=User.Roles.MAKER, designation="Officer",
              department="Engineering", department_ref=None):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.replace("_", " ").title(), last_name="T",
        role=role, designation=designation, department=department,
        department_ref=department_ref)


@pytest.fixture
def departments(db):
    """
    Two departments, resolved through the app registry rather than imported.

    `from leaves.models import Department` would be a real cross-module import,
    and test_independence.py refuses one anywhere under tasks/ — including here,
    deliberately: a boundary the test suite is exempt from is a boundary that
    gets crossed in the test suite first and in the application second.
    """
    Department = apps.get_model("leaves", "Department")
    engineering = Department.objects.create(name="Engineering", code="ENG")
    finance = Department.objects.create(name="Finance", code="FIN")
    return {"engineering": engineering, "finance": finance}


@pytest.fixture
def cast(db, departments):
    """
    The people a task needs.

    `hod` is the head of Engineering; `other_hod` heads Finance, and exists so
    every scope test can prove a department head is confined to their own
    department rather than merely that they can see something.
    """
    eng, fin = departments["engineering"], departments["finance"]
    employee = make_user("tsk_employee", department_ref=eng)
    peer = make_user("tsk_peer", department_ref=eng)
    hod = make_user("tsk_hod", User.Roles.CHECKER, "Department Head",
                    department_ref=eng)
    eng.head = hod
    eng.save(update_fields=["head"])
    other_hod = make_user("tsk_other_hod", User.Roles.CHECKER, "Department Head",
                          department="Finance", department_ref=fin)
    fin.head = other_hod
    fin.save(update_fields=["head"])
    return {
        "employee": employee,
        "peer": peer,
        "hod": hod,
        "other_hod": other_hod,
        "outsider": make_user("tsk_outsider", department="Finance",
                              department_ref=fin),
        # HR is the APPROVER role — see tasks/permissions.is_hr.
        "hr": make_user("tsk_hr", User.Roles.APPROVER, "HR Manager",
                        department_ref=eng),
        "admin": make_user("tsk_admin", User.Roles.ADMIN, "Administrator"),
    }


@pytest.fixture
def auth(api):
    """Log a user in and return the client, so a test reads as `auth(user)`."""
    def _auth(user):
        api.force_authenticate(user=user)
        return api
    return _auth


def build_task(creator, assignees=(), *, status=Task.Status.DRAFT, reviewer=None,
               due_date=None, department=None, title="Prepare the quarterly return"):
    """
    A task at any point in its lifecycle, driven through the real engine.

    Statuses are reached by RUNNING the transitions, never by assigning to
    `status` — a fixture that set the field directly would happily build states
    the engine cannot produce, and the tests would then pass against a system
    that does not exist.
    """
    from tasks.services import user_snapshot

    task = Task.objects.create(
        task_number=generate_task_number(), title=title,
        description="Because it is due.", created_by=creator,
        created_by_name=user_snapshot(creator)["name"],
        reviewer=reviewer, reviewer_name=user_snapshot(reviewer)["name"],
        due_date=due_date, department=department,
        department_name=department.name if department else "",
    )
    workflow.record_creation(task, creator)
    if assignees:
        workflow.set_assignees(task, list(assignees), creator)
    if status == Task.Status.DRAFT:
        return task

    workflow.assign(task, creator)
    if status == Task.Status.ASSIGNED:
        return task

    worker = list(assignees)[0]
    # EVERYBODY accepts, not just the first person
    # (Phase TASK-MULTI-ASSIGNEE-COLLABORATION). A shared task stays Assigned
    # until they all have, so accepting for one of three would leave every
    # fixture that asks for a later status stuck at Pending Acceptance.
    for person in assignees:
        workflow.accept(task, person)
    if status == Task.Status.ACCEPTED:
        return task
    if status == Task.Status.BLOCKED:
        workflow.block(task, worker, "Waiting on the auditor's figures.")
        return task

    workflow.start(task, worker)
    if status == Task.Status.IN_PROGRESS:
        return task

    workflow.submit_for_review(task, worker)
    if status == Task.Status.UNDER_REVIEW:
        return task

    workflow.approve_review(task, reviewer or creator)
    if status == Task.Status.COMPLETED:
        return task

    workflow.close(task, reviewer or creator)
    return task


@pytest.fixture
def make_task(db):
    return build_task


@pytest.fixture
def today():
    from django.utils import timezone
    return timezone.localdate()


@pytest.fixture
def tomorrow(today):
    return today + datetime.timedelta(days=1)
