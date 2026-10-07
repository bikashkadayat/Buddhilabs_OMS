"""
Role-based access control.

The specification's four roles, tested as CAPABILITIES rather than as page
visibility:

    Employee        view own tasks, update progress, comment, upload evidence
    Department Head assign, review, close  — within their own department
    HR              assign organisation-wide, review, monitor completion
    Admin           full access

The two properties worth more than any individual rule are that a task nobody
routed to you is invisible, and that a department head is confined to their own
department. Both have their own test below, asserted from the API rather than
from the permission functions, because the API is what an attacker reaches.
"""
import pytest

from tasks.models import Task
from tasks import permissions as perms

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status


def ids(response):
    rows = response.data.get("results", response.data)
    return {row["id"] for row in rows}


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("who", ["employee", "hod", "hr", "admin"])
def test_everybody_may_raise_a_task(cast, auth, tomorrow, who):
    """
    Creation was manager-only until TASK-MANAGEMENT-ASANA-MODEL, then split by
    task KIND, and TASK-SIMPLIFICATION removed the kinds too: one task, raised
    by anybody, for anybody. The rules about WHO may be named on it, and who
    reviews it, live in test_simplified.py.
    """
    response = auth(cast[who]).post(LIST, {
        "title": "Reconcile the petty cash",
        "due_date": str(tomorrow),
        # Both people are mandatory: Assigned To since TASK-SIMPLIFICATION and
        # Reviewer since TASK-REVIEWER-SELECTION. Raising a task for yourself
        # means naming yourself and choosing who approves it.
        "assignee_ids": [str(cast[who].id)],
        "reviewer": str(cast["other_hod"].id),
    }, format="json")
    assert response.status_code == 201, response.data
    assert response.data["assignee_names"] == [cast[who].get_full_name()]


def test_the_employee_picker_is_open_to_everybody(cast, auth):
    """
    It was restricted while only managers could assign work. The creator of a
    task now chooses both its assignee and its reviewer
    (Phase TASK-SIMPLIFICATION), so gating the search would leave most people
    unable to fill in either field. It returns names and designations - never
    contact details, never anything about employment.
    """
    assert auth(cast["employee"]).get(f"{LIST}employees/").status_code == 200
    assert auth(cast["hod"]).get(f"{LIST}employees/").status_code == 200


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------
def test_an_employee_sees_only_tasks_that_involve_them(cast, auth, make_task):
    mine = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    theirs = make_task(cast["hod"], [cast["peer"]], status=Status.ASSIGNED)

    visible = ids(auth(cast["employee"]).get(LIST))
    assert str(mine.id) in visible
    assert str(theirs.id) not in visible


def test_an_unrelated_employee_gets_404_not_403_on_a_task_detail(
        cast, auth, make_task):
    """
    404, because the queryset excludes it. A 403 would confirm that a task with
    that id exists, which is an enumeration oracle over the whole table.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    assert auth(cast["outsider"]).get(f"{LIST}{task.id}/").status_code == 404


def test_a_department_head_sees_their_own_department_and_not_another(
        cast, auth, make_task, departments):
    ours = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                     department=departments["engineering"])
    theirs = make_task(cast["other_hod"], [cast["outsider"]],
                       status=Status.ASSIGNED,
                       department=departments["finance"])

    visible = ids(auth(cast["hod"]).get(LIST))
    assert str(ours.id) in visible
    assert str(theirs.id) not in visible


def test_a_department_head_cannot_see_another_departments_draft(
        cast, auth, make_task, departments):
    draft = make_task(cast["other_hod"], [cast["outsider"]],
                      department=departments["finance"])
    assert auth(cast["hod"]).get(f"{LIST}{draft.id}/").status_code == 404


def test_a_draft_is_invisible_to_the_department_until_it_is_assigned(
        cast, auth, make_task, departments):
    """A draft is nobody's business but its author's."""
    draft = make_task(cast["hr"], [cast["employee"]],
                      department=departments["engineering"])
    assert str(draft.id) not in ids(auth(cast["hod"]).get(LIST))


@pytest.mark.parametrize("who", ["hr", "admin"])
def test_hr_and_admin_read_everything(cast, auth, make_task, departments, who):
    a = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED,
                  department=departments["engineering"])
    b = make_task(cast["other_hod"], [cast["outsider"]], status=Status.ASSIGNED,
                  department=departments["finance"])
    visible = ids(auth(cast[who]).get(LIST))
    assert {str(a.id), str(b.id)} <= visible


# ---------------------------------------------------------------------------
# Doing the work
# ---------------------------------------------------------------------------
def test_only_the_assignee_reports_progress_never_the_manager(
        cast, auth, make_task):
    """
    A progress number a manager typed on somebody else's behalf is not a report,
    and the evidence beside it would disagree with it.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    url = f"{LIST}{task.id}/progress/"
    assert auth(cast["hod"]).post(url, {"progress_percent": 80},
                                  format="json").status_code == 403
    assert auth(cast["employee"]).post(url, {"progress_percent": 80},
                                       format="json").status_code == 200


def test_only_an_assignee_submits_for_review(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    url = f"{LIST}{task.id}/submit-for-review/"
    assert auth(cast["peer"]).post(url, {}, format="json").status_code == 404
    assert auth(cast["hod"]).post(url, {}, format="json").status_code == 403
    assert auth(cast["employee"]).post(url, {}, format="json").status_code == 200


# ---------------------------------------------------------------------------
# Reviewing and closing
# ---------------------------------------------------------------------------
def test_the_assignee_cannot_approve_their_own_work(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    assert auth(cast["employee"]).post(
        f"{LIST}{task.id}/approve/", {}, format="json").status_code == 403


def test_the_selected_reviewer_approves(cast, auth, make_task):
    """
    Phase TASK-REVIEWER-SELECTION: the approver is whoever the creator named.
    It used to be anybody with owner authority, which meant the chosen reviewer
    could be overruled by somebody who was never asked.
    """
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    assert auth(cast["hod"]).post(f"{LIST}{task.id}/approve/", {},
                                  format="json").status_code == 200


def test_hr_cannot_approve_a_review_they_were_not_chosen_for(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    assert auth(cast["hr"]).post(f"{LIST}{task.id}/approve/", {},
                                 format="json").status_code == 403


def test_an_admin_may_still_unstick_a_review(cast, auth, make_task):
    """
    The one override. A named reviewer who has left would otherwise strand the
    work, and the audit row says who stepped in.
    """
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    assert auth(cast["admin"]).post(f"{LIST}{task.id}/approve/", {},
                                    format="json").status_code == 200


def test_a_department_head_cannot_approve_another_departments_task(
        cast, auth, make_task, departments):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW,
                     department=departments["engineering"])
    # 404: the other department's head cannot even see it.
    assert auth(cast["other_hod"]).post(
        f"{LIST}{task.id}/approve/", {}, format="json").status_code == 404


def test_the_person_who_did_the_work_can_never_close_it(cast, auth, make_task):
    """
    A task closed by the person who did it is unverified work wearing a verified
    label. This holds even when that person is HR and created the task
    themselves — the check is "are you an assignee", not "what is your role".
    """
    task = make_task(cast["hr"], [cast["hr"]], reviewer=cast["hr"],
                     status=Status.COMPLETED)
    assert auth(cast["hr"]).post(f"{LIST}{task.id}/close/", {},
                                 format="json").status_code == 403
    assert auth(cast["admin"]).post(f"{LIST}{task.id}/close/", {},
                                    format="json").status_code == 200


def test_rework_requires_a_reason(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)
    url = f"{LIST}{task.id}/request-rework/"
    assert auth(cast["hod"]).post(url, {}, format="json").status_code == 400
    assert auth(cast["hod"]).post(url, {"reason": "no"},
                                 format="json").status_code == 400
    assert auth(cast["hod"]).post(
        url, {"reason": "Page two cites last quarter's figures."},
        format="json").status_code == 200


# ---------------------------------------------------------------------------
# Terminal tasks are enforced, not styled
# ---------------------------------------------------------------------------
def test_a_closed_task_refuses_edits_and_deletes_from_everyone(
        cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.CLOSED)
    for who in ("employee", "hod", "hr", "admin"):
        client = auth(cast[who])
        assert client.patch(f"{LIST}{task.id}/", {"title": "Rewritten"},
                            format="json").status_code == 403
        assert client.delete(f"{LIST}{task.id}/").status_code == 403
    task.refresh_from_db()
    assert task.title != "Rewritten"


def test_only_an_unassigned_draft_can_be_deleted(cast, auth, make_task):
    draft = make_task(cast["hod"], [])
    assigned = make_task(cast["hod"], [cast["employee"]], status=Status.ASSIGNED)
    assert auth(cast["hod"]).delete(f"{LIST}{draft.id}/").status_code == 204
    assert auth(cast["hod"]).delete(f"{LIST}{assigned.id}/").status_code == 403


# ---------------------------------------------------------------------------
# Capability flags match the enforcement
# ---------------------------------------------------------------------------
def test_the_capability_flags_agree_with_what_the_api_allows(
        cast, auth, make_task):
    """
    The UI renders its buttons from these flags, so a flag that disagreed with
    the guard would either hide a legal action or offer an illegal one.
    """
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.UNDER_REVIEW)

    employee_flags = auth(cast["employee"]).get(
        f"{LIST}{task.id}/").data["capabilities"]
    assert employee_flags["can_review"] is False
    assert employee_flags["can_edit"] is False
    assert employee_flags["can_comment"] is True

    hod_flags = auth(cast["hod"]).get(f"{LIST}{task.id}/").data["capabilities"]
    assert hod_flags["can_review"] is True
    assert hod_flags["can_update_progress"] is False


def test_unauthenticated_callers_get_nothing(api):
    assert api.get(LIST).status_code in (401, 403)


# ---------------------------------------------------------------------------
# The permission helpers themselves
# ---------------------------------------------------------------------------
def test_role_predicates_use_the_stored_values_not_the_labels(cast):
    """
    The stored values are maker/checker/approver/admin with business labels on
    top. `Roles.HR` does not exist; reaching for it raises AttributeError, which
    is why these helpers exist at all.
    """
    assert perms.is_hr(cast["hr"]) and not perms.is_hr(cast["hod"])
    assert perms.is_department_head(cast["hod"])
    assert perms.is_admin(cast["admin"])
    assert perms.has_org_wide_read(cast["hr"])
    assert not perms.has_org_wide_read(cast["hod"])
    assert perms.can_assign(cast["hod"]) and not perms.can_assign(cast["employee"])
