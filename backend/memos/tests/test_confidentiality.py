"""
Memo read access (Phase 10 visibility model).

Superseded rule: any submitted non-sensitive memo used to be readable by every
authenticated user in every department. That is no longer the case - it made
"Memo Inbox" and "Department Memo" cosmetic filters over an org-wide pool and
contradicted Phase 10's "Employee: view own memo". The rule asserted here now is:

    Employee          -> own memos + memos they are routed on
    Department Head   -> the above + their own department's non-sensitive memos
    HR / Admin        -> everything

Sensitive (HR/Financial) memos stay restricted to author + routed assignees +
HR/Admin, even from the author's own department head.
"""
import pytest

from memos.models import Memo, MemoWorkflowStep
from memos.services import generate_memo_number
from users.models import User


def _user(username, role, department):
    return User.objects.create_user(
        username=username, email=f"{username}@nif.test", password="pass12345",
        first_name=username.capitalize(), last_name="T", role=role, department=department,
    )


def _memo(author, memo_type, status, reviewer=None):
    """
    Create a memo, optionally with `reviewer` routed on it. Routing is a matrix
    row now, so being "the assignee" means holding a step - which is exactly the
    condition the visibility rule tests.
    """
    memo = Memo.objects.create(
        subject="S", memo_type=memo_type, status=status,
        created_by=author, memo_number=generate_memo_number(memo_type),
    )
    if reviewer is not None:
        MemoWorkflowStep.objects.create(
            memo=memo, sequence=1, assignee=reviewer, role_type="reviewer",
            status="active", designation=reviewer.designation or "",
            department_label=reviewer.department or "",
        )
    return memo


@pytest.mark.django_db
def test_submitted_memo_is_not_visible_outside_its_department(api, db):
    """
    A submitted, non-sensitive memo is visible to its own department head but
    NOT to a department head elsewhere in the organisation. This is the test
    that previously asserted the opposite (org-wide visibility).
    """
    eng_maker = _user("engmaker", User.Roles.MAKER, "Engineering")
    eng_checker = _user("engchecker", User.Roles.CHECKER, "Engineering")
    hr_checker = _user("hrchecker", User.Roles.CHECKER, "HRdept")
    memo = _memo(eng_maker, Memo.MemoType.GENERAL, Memo.Status.DRAFT_FOR_REVIEW, reviewer=eng_checker)

    # Same-department checker (also the assignee) sees it.
    api.force_authenticate(eng_checker)
    assert api.get(f"/api/v1/memos/{memo.id}/").status_code == 200

    # A checker in a DIFFERENT department is now out of scope.
    api.force_authenticate(hr_checker)
    assert api.get(f"/api/v1/memos/{memo.id}/").status_code == 404


@pytest.mark.django_db
def test_unrelated_employee_cannot_see_another_employees_memo(api, db):
    """Phase 10: an employee sees their own memos, not a colleague's."""
    author = _user("emp_author", User.Roles.MAKER, "Engineering")
    stranger = _user("emp_stranger", User.Roles.MAKER, "Engineering")
    memo = _memo(author, Memo.MemoType.GENERAL, Memo.Status.DRAFT_FOR_REVIEW)

    api.force_authenticate(stranger)
    assert api.get(f"/api/v1/memos/{memo.id}/").status_code == 404
    ids = {m["id"] for m in api.get("/api/v1/memos/").data["results"]}
    assert str(memo.id) not in ids


@pytest.mark.django_db
def test_hr_retains_org_wide_read(api, db, approver):
    """HR owns the archive and the reports, so its org-wide read is retained."""
    maker = _user("m_far", User.Roles.MAKER, "Engineering")
    memo = _memo(maker, Memo.MemoType.GENERAL, Memo.Status.DRAFT_FOR_REVIEW)
    api.force_authenticate(approver)
    assert api.get(f"/api/v1/memos/{memo.id}/").status_code == 200


@pytest.mark.django_db
def test_draft_memo_stays_private_to_its_author(api, db):
    """Drafts are not shared: only the author (and admin) may see one."""
    author = _user("draftauthor", User.Roles.MAKER, "Engineering")
    other = _user("draftother", User.Roles.CHECKER, "HRdept")
    memo = _memo(author, Memo.MemoType.GENERAL, Memo.Status.DRAFT)

    api.force_authenticate(other)
    assert api.get(f"/api/v1/memos/{memo.id}/").status_code == 404

    api.force_authenticate(author)
    assert api.get(f"/api/v1/memos/{memo.id}/").status_code == 200


@pytest.mark.django_db
def test_same_dept_checker_sees_nonsensitive_pool(api, db):
    maker = _user("m_eng", User.Roles.MAKER, "Engineering")
    assigned = _user("c_eng_a", User.Roles.CHECKER, "Engineering")
    other = _user("c_eng_b", User.Roles.CHECKER, "Engineering")
    memo = _memo(maker, Memo.MemoType.GENERAL, Memo.Status.DRAFT_FOR_REVIEW, reviewer=assigned)
    api.force_authenticate(other)  # same dept, not assigned
    assert api.get(f"/api/v1/memos/{memo.id}/").status_code == 200


@pytest.mark.django_db
def test_financial_memo_restricted_to_assigned(api, db):
    maker = _user("m_fin", User.Roles.MAKER, "Engineering")
    assigned = _user("c_fin_a", User.Roles.CHECKER, "Engineering")
    other = _user("c_fin_b", User.Roles.CHECKER, "Engineering")  # same dept!
    memo = _memo(maker, Memo.MemoType.CONFIDENTIAL, Memo.Status.DRAFT_FOR_REVIEW, reviewer=assigned)

    # Same department but NOT assigned -> a sensitive memo is invisible.
    api.force_authenticate(other)
    assert api.get(f"/api/v1/memos/{memo.id}/").status_code == 404

    # The assigned checker still sees it.
    api.force_authenticate(assigned)
    assert api.get(f"/api/v1/memos/{memo.id}/").status_code == 200


@pytest.mark.django_db
def test_hr_memo_not_in_list_for_unassigned_same_dept(api, db):
    maker = _user("m_hr", User.Roles.MAKER, "Engineering")
    assigned = _user("c_hr_a", User.Roles.CHECKER, "Engineering")
    other = _user("c_hr_b", User.Roles.CHECKER, "Engineering")
    memo = _memo(maker, Memo.MemoType.CONFIDENTIAL, Memo.Status.DRAFT_FOR_REVIEW, reviewer=assigned)
    api.force_authenticate(other)
    ids = {m["id"] for m in api.get("/api/v1/memos/").data["results"]}
    assert str(memo.id) not in ids


@pytest.mark.django_db
def test_admin_still_sees_sensitive_cross_dept(api, db, admin):
    maker = _user("m_x", User.Roles.MAKER, "Engineering")
    memo = _memo(maker, Memo.MemoType.CONFIDENTIAL, Memo.Status.DRAFT_FOR_REVIEW)
    api.force_authenticate(admin)
    assert api.get(f"/api/v1/memos/{memo.id}/").status_code == 200
