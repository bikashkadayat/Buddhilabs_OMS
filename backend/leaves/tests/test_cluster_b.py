"""
Cluster B — security / integrity verification.

M7: a Dept Head (checker) is scoped to their own department across
    LeaveDayRecord / summaries / calendar / TeamAttendance; cross-department
    ?user_id is denied (no data leak). HR/Admin keep org-wide access.
M4: concurrent applications on the same balance are race-safe (no over-spend,
    never negative, capped at allocation) via select_for_update.
M5: logout blacklists the refresh token server-side; the old token is rejected.
"""
import threading
from datetime import timedelta

import pytest
from django.db import connection
from rest_framework.test import APIClient

from users.models import User
from leaves.models import Leave, LeaveBalance
from .conftest import _user, MONDAY


@pytest.fixture(scope="module", autouse=True)
def restore_flushed_seed_data(django_db_setup, django_db_blocker):
    """Put back the migration-seeded leaves.* rows a transactional test flushes.

    test_concurrent_apply_cannot_exceed_allocation below is transactional, so
    Django FLUSHES every table on its teardown — taking LeaveType/Holiday/
    EntitlementRule (leaves/migrations 0005, 0010, 0013) with it. Restore them at
    module teardown so a module collected after this one can still resolve e.g.
    leave_type='annual'. The seed this module's own transactional test needs is
    guaranteed the same way by the flushing modules that precede it
    (biometric/tests/test_realtime.py, inventory/tests/test_lifecycle_notifications.py).
    leaves.* and tenancy.* are restored, parents first (Phase S2);
    framework tables are rebuilt by post_migrate.
    """
    import json

    from django.db import connection

    with django_db_blocker.unblock():
        # tenancy.* AS WELL AS leaves.*, AND TENANCY FIRST (Phase S2).
        #
        # Every leaves row now carries a non-null FK to tenancy.Organization.
        # Restoring leaves.* alone re-inserts children whose parent the flush
        # has just deleted, and the next constraint check fails with:
        #
        #     IntegrityError: The row in table 'leaves_holiday' ... has an
        #     invalid foreign key
        #
        # The sort puts the parents in first; it is stable, so the relative
        # order within each group is unchanged.
        rows = [
            obj for obj in json.loads(connection.creation.serialize_db_to_string())
            if obj["model"].startswith(("leaves.", "tenancy."))
        ]
        snapshot = json.dumps(
            sorted(rows, key=lambda obj: not obj["model"].startswith("tenancy.")))

    yield

    with django_db_blocker.unblock():
        connection.creation.deserialize_db_from_string(snapshot)


def _emp(username, role, dept):
    return _user(username, role, department=dept)


def _make_leave(emp, start, end, status=Leave.Status.APPROVED, approver=None):
    return Leave.objects.create(
        user=emp, leave_type="annual", reason="x",
        start_date=start, end_date=end, status=status, approver=approver,
    )


# ---------------------------------------------------------------------------
# M7 — cross-department scoping
# ---------------------------------------------------------------------------
@pytest.fixture
def two_depts(db):
    head_a = _emp("cb_head_a", User.Roles.CHECKER, "DEPTA")
    emp_a = _emp("cb_emp_a", User.Roles.MAKER, "DEPTA")
    emp_b = _emp("cb_emp_b", User.Roles.MAKER, "DEPTB")
    hr = _emp("cb_hr", User.Roles.APPROVER, "DEPTA")
    _make_leave(emp_a, MONDAY, MONDAY + timedelta(days=1))
    _make_leave(emp_b, MONDAY, MONDAY + timedelta(days=1))
    return head_a, emp_a, emp_b, hr


@pytest.mark.django_db
def test_checker_cannot_read_other_department_day_records(two_depts):
    head_a, emp_a, emp_b, hr = two_depts
    c = APIClient(); c.force_authenticate(head_a)

    # Own-department employee: allowed.
    r_own = c.get(f"/api/v1/leave-day-records/?user_id={emp_a.id}")
    assert r_own.status_code == 200
    # Cross-department employee: denied, no data leak.
    r_other = c.get(f"/api/v1/leave-day-records/?user_id={emp_b.id}")
    assert r_other.status_code == 403

    # Unfiltered list is scoped to the checker's own department: it returns the
    # same records as the own-department employee (emp_a is DEPTA's only maker),
    # i.e. emp_b's records are excluded.
    def _count(resp):
        return resp.data["count"] if isinstance(resp.data, dict) and "count" in resp.data else len(resp.data)
    all_count = _count(c.get("/api/v1/leave-day-records/"))
    assert all_count == _count(r_own) == 2


@pytest.mark.django_db
def test_checker_cannot_read_other_department_calendar(two_depts):
    head_a, emp_a, emp_b, hr = two_depts
    c = APIClient(); c.force_authenticate(head_a)
    assert c.get(f"/api/v1/leaves/calendar/?user_id={emp_a.id}").status_code == 200
    assert c.get(f"/api/v1/leaves/calendar/?user_id={emp_b.id}").status_code == 403


@pytest.mark.django_db
def test_checker_summaries_scoped(two_depts):
    head_a, emp_a, emp_b, hr = two_depts
    c = APIClient(); c.force_authenticate(head_a)
    assert c.get(f"/api/v1/monthly-summaries/?user_id={emp_b.id}").status_code == 403
    assert c.get(f"/api/v1/weekly-summaries/?user_id={emp_b.id}").status_code == 403


@pytest.mark.django_db
def test_team_attendance_scoped_to_own_department(two_depts):
    head_a, emp_a, emp_b, hr = two_depts
    month = f"{MONDAY.year}-{MONDAY.month:02d}"
    c = APIClient(); c.force_authenticate(head_a)
    data = c.get(f"/api/v1/leaves/team-attendance/?month={month}").data
    depts = {row["user"]["department"] for row in data["team"]}
    names = {row["user"]["full_name"] for row in data["team"]}
    assert "DEPTB" not in depts                          # other department excluded
    assert any("Cb_emp_a" in n for n in names)           # own department present


@pytest.mark.django_db
def test_hr_keeps_org_wide_access(two_depts):
    head_a, emp_a, emp_b, hr = two_depts
    c = APIClient(); c.force_authenticate(hr)
    # HR may read across departments.
    assert c.get(f"/api/v1/leave-day-records/?user_id={emp_b.id}").status_code == 200
    assert c.get(f"/api/v1/leaves/calendar/?user_id={emp_b.id}").status_code == 200


# ---------------------------------------------------------------------------
# M4 — concurrency / race-safety
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Tests select_for_update, which SQLite does not implement — it "
           "serialises writes with a database-level lock instead. The test "
           "cannot pass here, so it is skipped rather than left to fail: a "
           "permanently-red test in the development suite trains people to "
           "ignore red, and the next real regression goes with it. Production "
           "is PostgreSQL, where this runs.")
@pytest.mark.django_db(transaction=True)
def test_concurrent_apply_cannot_exceed_allocation():
    """Two threads apply the SAME 8-working-day annual leave (Category A = 12)
    simultaneously. Locking must let only one through; used_so_far must never
    exceed the allocation or go negative."""
    emp = _emp("cb_race", User.Roles.MAKER, "RACE")
    hr = _emp("cb_race_hr", User.Roles.APPROVER, "RACE")
    start, end = MONDAY, MONDAY + timedelta(days=8)  # 8 working days (one Saturday)

    results = {}
    barrier = threading.Barrier(2)

    def worker(idx):
        try:
            barrier.wait()
            c = APIClient(); c.force_authenticate(emp)
            r = c.post("/api/v1/leaves/", {
                "leave_type": "annual", "start_date": str(start), "end_date": str(end),
                "reason": "race", "approver": str(hr.id),
            }, format="json")
            results[idx] = r.status_code
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    bal = LeaveBalance.objects.get(user=emp, leave_type="annual", year=start.year)
    # Invariants: never negative, never over allocation. 8+8=16 would breach 12.
    assert 0 <= bal.used_so_far <= bal.total_allocated
    assert bal.used_so_far == 8            # exactly one application committed
    assert sum(1 for v in results.values() if v == 201) == 1

    # cleanup rows created in this transaction=True test
    Leave.objects.filter(user=emp).delete()
    LeaveBalance.objects.filter(user=emp).delete()
    User.objects.filter(username__in=["cb_race", "cb_race_hr"]).delete()


# ---------------------------------------------------------------------------
# M5 — logout blacklists the refresh token
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_logout_blacklists_refresh_token():
    user = _emp("cb_logout", User.Roles.MAKER, "DEPTA")
    user.set_password("pass12345"); user.save()

    c = APIClient()
    login = c.post("/api/v1/auth/login/", {"email": user.email, "password": "pass12345"}, format="json")
    assert login.status_code == 200, login.content
    access, refresh = login.data["access"], login.data["refresh"]

    # The refresh token works before logout.
    ok = APIClient().post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")
    assert ok.status_code == 200

    c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    out = c.post("/api/v1/auth/logout/", {"refresh": ok.data["refresh"]}, format="json")
    assert out.status_code == 205

    # The (rotated) refresh token is rejected after logout.
    rejected = APIClient().post("/api/v1/auth/refresh/", {"refresh": ok.data["refresh"]}, format="json")
    assert rejected.status_code == 401


# ---------------------------------------------------------------------------
# M4 (extension) — the DECISION path, not just the apply path.
#
# The concurrency case above protects the balance. This one protects the leave's
# own state transition, which was NOT race-safe: all three decision actions read
# the leave with an unlocked get_object(), evaluated the status guard, and only
# then opened a transaction:
#
#     leave = self.get_object()               # unlocked read
#     if leave.status != PENDING: return 400  # guard, OUTSIDE the transaction
#     with transaction.atomic():              # transaction opens after the check
#
# Two reviewers (or one double-submitted form) could both read PENDING, both pass
# the guard, and both write — the second silently overwriting the first
# reviewer's identity and timestamp, so the granted leave named the wrong
# approver in the record, the PDF and the audit trail. Verified against the
# pre-fix code: both threads returned 200.
#
# Lives in this file deliberately: transaction=True TRUNCATEs rather than rolls
# back, taking the migration-seeded LeaveType/Holiday rows with it, so a
# transactional test has to sit late enough in the run not to strand its
# neighbours — which is exactly why the case above is here too.
# ---------------------------------------------------------------------------
# Same guard as the race test above, which this one was missing. Without it
# both threads collide on SQLite's TABLE lock and BOTH fail with "database
# table is locked: leaves_leave" -- a red that says nothing about the product,
# raised nondeterministically: it passed inside one full run and then failed
# 3/3 in isolation on identical code.
@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Tests select_for_update, which SQLite does not implement — see "
           "the sibling race test above for why this is skipped rather than "
           "left to fail. Production and CI are PostgreSQL, where it runs.")
@pytest.mark.django_db(transaction=True)
def test_two_dept_heads_cannot_both_grant_the_same_leave():
    from leaves.models import Department, LeaveType as LeaveTypeModel

    # Self-sufficient on purpose. transaction=True TRUNCATEs rather than rolls
    # back, so whichever transactional test runs first takes the migration-seeded
    # LeaveType rows with it and every later one finds none — the signal layer
    # then raises "No LeaveType matches 'annual'" from inside post_save, which
    # reads like a product bug and is not. Creating what this test needs makes it
    # independent of where it lands in the run order.
    LeaveTypeModel.objects.get_or_create(
        code="annual", defaults={"name": "Annual Leave", "default_days_per_year": 12})

    dept = Department.objects.create(name="Race Dept", code="RACE")
    applicant = _user("race_emp", User.Roles.MAKER, department="RACE", department_ref=dept)
    head_a = _user("race_head_a", User.Roles.CHECKER, department="RACE", department_ref=dept)
    head_b = _user("race_head_b", User.Roles.CHECKER, department="RACE", department_ref=dept)

    leave = Leave.objects.create(
        user=applicant, leave_type="annual", reason="race",
        start_date=MONDAY, end_date=MONDAY + timedelta(days=1),
        status=Leave.Status.PENDING)

    url = f"/api/v1/leaves/{leave.id}/dept-head-review/"
    barrier = threading.Barrier(2)
    results = {}

    def review(actor, key):
        client = APIClient()
        client.force_authenticate(actor)
        barrier.wait()                      # release both threads together
        try:
            results[key] = client.post(url, {"decision": "approve"}, format="json").status_code
        except Exception as exc:            # noqa: BLE001
            results[key] = repr(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=review, args=(head_a, "a")),
               threading.Thread(target=review, args=(head_b, "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    leave.refresh_from_db()
    codes = sorted(str(c) for c in results.values())

    assert results.get("a") is not None and results.get("b") is not None, results
    assert codes.count("200") == 1, (
        f"expected exactly one successful grant, got {results} — both succeeding "
        f"means the second reviewer overwrote the first")

    assert leave.status == Leave.Status.APPROVED
    winner = head_a if results.get("a") == 200 else head_b
    assert leave.department_head_reviewer_id == winner.id, (
        "the leave records a different reviewer than the one whose request succeeded")
