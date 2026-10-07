"""Department-Head (stored role ``checker``) list scoping.

LeaveViewSet.get_queryset applies the department filter to a Dept Head's review
clause only when ``user.department_ref_id`` is set::

    review = Q(status=Leave.Status.PENDING)
    if user.department_ref_id:
        review &= Q(user__department_ref_id=user.department_ref_id)

With no department the narrowing clause is simply skipped, so the review clause
degrades to "every pending leave in the company". That is a fail-open default,
and ``User.department_ref`` is ``on_delete=SET_NULL`` — deleting or merging a
Department nulls it for every member, so this state is reachable through an
ordinary admin action, not just a data-entry slip.
"""
from datetime import date, timedelta

import pytest

from leaves.models import Department, Leave
from users.models import User

from .conftest import MONDAY, _user


@pytest.fixture
def two_departments(db):
    return (
        Department.objects.create(name="Engineering", code="ENGX"),
        Department.objects.create(name="Finance", code="FINX"),
    )


def _pending_leave(user):
    return Leave.objects.create(
        user=user, leave_type="annual", reason="unrelated department",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=1),
        status=Leave.Status.PENDING,
    )


@pytest.mark.django_db
def test_head_with_department_cannot_see_other_departments_pending(api, two_departments):
    """Baseline: the filter works when the head HAS a department."""
    eng, fin = two_departments
    head = _user("headeng", User.Roles.CHECKER, department_ref=eng)
    outsider = _user("finstaff", User.Roles.MAKER, department="FIN", department_ref=fin)
    leak = _pending_leave(outsider)

    api.force_authenticate(head)
    resp = api.get("/api/v1/leaves/")
    assert resp.status_code == 200
    ids = {row["id"] for row in (resp.data.get("results", resp.data))}
    assert str(leak.id) not in {str(i) for i in ids}


@pytest.mark.django_db
def test_head_without_department_must_not_see_other_departments_pending(api, two_departments):
    """A Dept Head whose department_ref is NULL must not gain company-wide read.

    Reproduces the SET_NULL path: the head's department is deleted, which nulls
    department_ref, and the review clause stops being narrowed.
    """
    eng, fin = two_departments
    head = _user("headnull", User.Roles.CHECKER, department_ref=eng)
    outsider = _user("finstaff2", User.Roles.MAKER, department="FIN", department_ref=fin)
    leak = _pending_leave(outsider)

    # An ordinary admin action: the head's department is removed.
    eng.delete()
    head.refresh_from_db()
    assert head.department_ref_id is None, "precondition: SET_NULL nulled the FK"

    api.force_authenticate(head)
    resp = api.get("/api/v1/leaves/")
    assert resp.status_code == 200
    ids = {str(row["id"]) for row in (resp.data.get("results", resp.data))}
    assert str(leak.id) not in ids, (
        "Dept Head with no department read a pending leave from another "
        "department — the review clause fell open to every pending row"
    )


@pytest.mark.django_db
def test_head_with_no_department_on_either_axis_sees_only_own_and_routed(api, two_departments):
    """Strictest case: no structured ref AND no legacy string => no review claim.

    The head must still see their OWN applications and anything explicitly routed
    to them as `approver` — failing closed must not blank a queue they are
    genuinely responsible for.
    """
    eng, fin = two_departments
    head = _user("headnone", User.Roles.CHECKER, department="", department_ref=None)
    outsider = _user("finstaff3", User.Roles.MAKER, department="FIN", department_ref=fin)
    hidden = _pending_leave(outsider)

    routed = Leave.objects.create(
        user=outsider, leave_type="annual", reason="routed to this head",
        start_date=MONDAY, end_date=MONDAY, status=Leave.Status.PENDING,
        approver=head)

    api.force_authenticate(head)
    resp = api.get("/api/v1/leaves/")
    assert resp.status_code == 200
    ids = {str(row["id"]) for row in (resp.data.get("results", resp.data))}

    assert str(hidden.id) not in ids, "departmentless head saw an unrelated pending leave"
    assert str(routed.id) in ids, "failing closed wrongly hid a leave routed to this head"
