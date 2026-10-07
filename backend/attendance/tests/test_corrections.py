"""The attendance correction workflow: employee -> department head -> HR.

The assertion that matters most is that an applied correction **survives
re-derivation**. Corrections are written with ``source=HR``, which the
derivation engine already treats as untouchable, so a later punch import or
``rederive_attendance`` run cannot silently undo an HR decision.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from attendance.models import Attendance, AttendanceCorrectionRequest
from attendance.workforce import routing, services as workflow
from audit.models import AuditLog
from leaves.models import Department
from users.models import User

pytestmark = pytest.mark.django_db

URL = "/api/v1/workforce/corrections/"
Status = AttendanceCorrectionRequest.Status


@pytest.fixture
def head(db, dept, employee):
    """A department head who is not the employee under test."""
    from .conftest import make_user

    manager = make_user("att_dept_head", User.Roles.CHECKER, dept)
    dept.head = manager
    dept.save(update_fields=["head"])
    return manager


@pytest.fixture
def nif_rules(employee, a_weekday):
    """Pin the employee to the NIF arrival rules whatever the fixture date.

    The cutover migration applies them only from its go-live date onward, which
    is exactly the point — but a test asserting the rules themselves must not
    depend on where `a_weekday` falls relative to that.
    """
    from datetime import date as _date, time

    from attendance.models import AttendancePolicy, PolicyAssignment

    policy = AttendancePolicy.objects.create(
        name="NIF rules (corrections test)", office_start_time=time(10, 0),
        late_after_time=time(11, 45), half_day_after_time=time(13, 0))
    PolicyAssignment.objects.create(
        policy=policy, scope=PolicyAssignment.Scope.USER, user=employee,
        effective_from=_date(2000, 1, 1))
    return policy


@pytest.fixture
def submit(employee, a_weekday, local):
    def _submit(user=None, day=None, check_in=(10, 0), check_out=(18, 30),
                reason="Device missed my punch", **extra):
        user = user or employee
        day = day or a_weekday
        correction = AttendanceCorrectionRequest(
            employee=user, attendance_date=day, reason=reason,
            requested_check_in=local(day.year, day.month, day.day, *check_in)
            if check_in else None,
            requested_check_out=local(day.year, day.month, day.day, *check_out)
            if check_out else None,
            **extra)
        return workflow.submit(correction, actor=user)
    return _submit


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------
def test_a_request_routes_to_the_department_head(employee, head, submit):
    correction = submit()
    assert correction.manager_id == head.pk
    assert correction.status == Status.PENDING


def test_a_request_falls_back_to_any_checker_in_the_department(employee, dept_head, submit):
    """No designated head, but the department has a checker."""
    correction = submit()
    assert correction.manager_id == dept_head.pk


def test_a_request_with_no_manager_skips_straight_to_hr(db, submit):
    """An employee with no department must not land in a queue nobody owns."""
    from .conftest import make_user

    orphan = make_user("att_orphan", User.Roles.MAKER, None)
    correction = submit(user=orphan)
    assert correction.manager is None
    assert correction.status == Status.MANAGER_APPROVED


def test_a_department_head_never_reviews_their_own_request(dept, head, submit):
    correction = submit(user=head)
    assert correction.manager_id != head.pk


def test_the_snapshot_is_taken_at_submit_time(employee, a_weekday, local, submit,
                                              make_attendance):
    make_attendance(a_weekday, check_in=local(a_weekday.year, a_weekday.month,
                                              a_weekday.day, 12, 30),
                    status=Attendance.Status.LATE)
    correction = submit()
    assert correction.had_attendance_row is True
    assert correction.previous_status == Attendance.Status.LATE
    assert correction.previous_check_in is not None


def test_the_snapshot_records_that_there_was_no_row(employee, submit):
    correction = submit()
    assert correction.had_attendance_row is False
    assert correction.previous_check_in is None


# --------------------------------------------------------------------------
# the two approval stages
# --------------------------------------------------------------------------
def test_the_department_head_approves_the_first_stage(employee, head, auth, submit):
    correction = submit()
    response = auth(head).post(f"{URL}{correction.id}/approve/", {"remarks": "confirmed"},
                               format="json")
    assert response.status_code == 200
    correction.refresh_from_db()
    assert correction.status == Status.MANAGER_APPROVED
    assert correction.manager_action_at is not None
    assert correction.manager_remarks == "confirmed"


def test_an_employee_cannot_approve_their_own_request(employee, head, auth, submit):
    correction = submit()
    assert auth(employee).post(f"{URL}{correction.id}/approve/", {},
                               format="json").status_code == 403


def test_a_manager_from_another_department_cannot_approve(employee, head, other_dept,
                                                          auth, submit):
    from .conftest import make_user

    outsider_head = make_user("att_other_head", User.Roles.CHECKER, other_dept)
    correction = submit()
    # 404, not 403: the queryset is department-scoped, so an outside manager
    # cannot even see the request. Stricter than a permission denial.
    assert auth(outsider_head).post(f"{URL}{correction.id}/approve/", {},
                                    format="json").status_code in (403, 404)


def test_hr_cannot_skip_the_manager_stage(employee, head, hr, auth, submit):
    """HR *may* act at the manager stage, but doing so advances one step —
    it does not apply the correction."""
    correction = submit()
    auth(hr).post(f"{URL}{correction.id}/approve/", {}, format="json")
    correction.refresh_from_db()
    assert correction.status == Status.MANAGER_APPROVED
    assert correction.applied_at is None


def test_a_manager_cannot_finalise_the_hr_stage(employee, head, auth, submit):
    correction = submit()
    workflow.manager_approve(correction, actor=head)
    assert auth(head).post(f"{URL}{correction.id}/approve/", {},
                           format="json").status_code == 403


def test_hr_approval_applies_the_correction(employee, head, hr, auth, submit, a_weekday):
    correction = submit(check_in=(10, 0), check_out=(18, 30))
    workflow.manager_approve(correction, actor=head)

    response = auth(hr).post(f"{URL}{correction.id}/approve/", {"remarks": "applied"},
                             format="json")
    assert response.status_code == 200

    correction.refresh_from_db()
    assert correction.status == Status.HR_APPROVED
    assert correction.applied_at is not None
    assert correction.hr_actor_id == hr.pk

    record = Attendance.objects.get(employee=employee, date=a_weekday)
    assert record.source == Attendance.Source.HR
    assert record.marked_by == Attendance.MarkedBy.HR
    assert record.check_in is not None
    assert record.status == Attendance.Status.PRESENT


def test_an_applied_correction_obeys_the_nif_arrival_rules(employee, head, hr, submit,
                                                           a_weekday, nif_rules):
    """A 12:30 correction must land as Late, exactly like a browser check-in or
    a device punch — the same single seam decides all three."""
    correction = submit(check_in=(12, 30), check_out=(18, 30))
    workflow.manager_approve(correction, actor=head)
    workflow.hr_approve(correction, actor=hr)

    record = Attendance.objects.get(employee=employee, date=a_weekday)
    assert record.status == Attendance.Status.LATE
    assert record.late_minutes == 45


def test_hr_can_state_an_explicit_status(employee, head, hr, submit, a_weekday):
    correction = submit(check_in=(10, 0), check_out=(18, 30),
                        requested_status=Attendance.Status.HALF_DAY)
    workflow.manager_approve(correction, actor=head)
    workflow.hr_approve(correction, actor=hr)
    assert Attendance.objects.get(
        employee=employee, date=a_weekday).status == Attendance.Status.HALF_DAY


def test_a_correction_cannot_request_work_from_home(employee, auth, a_weekday, local):
    """WORK_FROM_HOME stays derived from an approved request plus a real
    check-in; typing it into a correction would bypass both gates."""
    response = auth(employee).post(URL, {
        "attendance_date": a_weekday.isoformat(),
        "requested_check_in": local(a_weekday.year, a_weekday.month, a_weekday.day,
                                    10, 0).isoformat(),
        "requested_status": "wfh", "reason": "worked from home"}, format="json")
    assert response.status_code == 400


# --------------------------------------------------------------------------
# the guarantee: an applied correction survives re-derivation
# --------------------------------------------------------------------------
def test_an_applied_correction_survives_re_derivation(employee, head, hr, submit,
                                                      a_weekday, local):
    from biometric.derivation import derive_daily_attendance
    from biometric.models import AttendancePunch, BiometricDevice

    correction = submit(check_in=(9, 0), check_out=(18, 0))
    workflow.manager_approve(correction, actor=head)
    workflow.hr_approve(correction, actor=hr)
    corrected = Attendance.objects.get(employee=employee, date=a_weekday)

    # A device punch lands afterwards claiming a completely different day.
    device = BiometricDevice.objects.create(name="Late device", label="late-device")
    AttendancePunch.objects.create(
        device=device, employee_device_id="9", user=employee,
        timestamp=local(a_weekday.year, a_weekday.month, a_weekday.day, 14, 0),
        local_date=a_weekday, punch=0)
    derive_daily_attendance(employee, a_weekday)

    corrected.refresh_from_db()
    assert corrected.source == Attendance.Source.HR
    assert timezone.localtime(corrected.check_in).hour == 9, "the HR decision stands"
    assert corrected.punch_count == 1, "punch evidence is still recorded"


def test_a_forced_rederive_can_still_override(employee, head, hr, submit, a_weekday, local):
    """`rederive_attendance --force` is the documented escape hatch."""
    from biometric.derivation import derive_daily_attendance
    from biometric.models import AttendancePunch, BiometricDevice

    correction = submit(check_in=(9, 0), check_out=(18, 0))
    workflow.manager_approve(correction, actor=head)
    workflow.hr_approve(correction, actor=hr)

    device = BiometricDevice.objects.create(name="Force device", label="force-device")
    AttendancePunch.objects.create(
        device=device, employee_device_id="9", user=employee,
        timestamp=local(a_weekday.year, a_weekday.month, a_weekday.day, 14, 0),
        local_date=a_weekday, punch=0)
    record = derive_daily_attendance(employee, a_weekday, force=True)
    assert record.source == Attendance.Source.BIOMETRIC


# --------------------------------------------------------------------------
# reject / cancel / revert
# --------------------------------------------------------------------------
def test_the_department_head_can_reject(employee, head, auth, submit):
    correction = submit()
    response = auth(head).post(f"{URL}{correction.id}/reject/",
                               {"reason": "You were on leave"}, format="json")
    assert response.status_code == 200
    correction.refresh_from_db()
    assert correction.status == Status.REJECTED
    assert correction.rejection_reason == "You were on leave"


def test_rejecting_does_not_touch_attendance(employee, head, auth, submit, a_weekday):
    correction = submit()
    auth(head).post(f"{URL}{correction.id}/reject/", {"reason": "no"}, format="json")
    assert not Attendance.objects.filter(employee=employee, date=a_weekday).exists()


def test_an_employee_can_cancel_their_own_pending_request(employee, head, auth, submit):
    correction = submit()
    assert auth(employee).post(f"{URL}{correction.id}/cancel/", {},
                               format="json").status_code == 200
    correction.refresh_from_db()
    assert correction.status == Status.CANCELLED


def test_only_the_owner_can_cancel(employee, coworker, head, auth, submit):
    correction = submit()
    assert auth(coworker).post(f"{URL}{correction.id}/cancel/", {},
                               format="json").status_code in (403, 404)


def test_a_decided_request_cannot_be_decided_again(employee, head, hr, auth, submit):
    correction = submit()
    workflow.manager_approve(correction, actor=head)
    workflow.hr_approve(correction, actor=hr)
    assert auth(hr).post(f"{URL}{correction.id}/approve/", {},
                         format="json").status_code == 400


def test_revert_restores_the_snapshot(employee, head, hr, auth, submit, a_weekday,
                                      local, make_attendance):
    make_attendance(a_weekday, check_in=local(a_weekday.year, a_weekday.month,
                                              a_weekday.day, 12, 30),
                    status=Attendance.Status.LATE)
    correction = submit(check_in=(9, 0), check_out=(18, 0))
    workflow.manager_approve(correction, actor=head)
    workflow.hr_approve(correction, actor=hr)
    assert timezone.localtime(Attendance.objects.get(
        employee=employee, date=a_weekday).check_in).hour == 9

    response = auth(hr).post(f"{URL}{correction.id}/revert/",
                             {"reason": "raised in error"}, format="json")
    assert response.status_code == 200

    record = Attendance.objects.get(employee=employee, date=a_weekday)
    assert timezone.localtime(record.check_in).hour == 12
    assert record.status == Attendance.Status.LATE


def test_revert_deletes_a_row_the_correction_created(employee, head, hr, submit,
                                                     a_weekday):
    correction = submit()
    workflow.manager_approve(correction, actor=head)
    workflow.hr_approve(correction, actor=hr)
    assert Attendance.objects.filter(employee=employee, date=a_weekday).exists()

    workflow.revert(correction, actor=hr, reason="undo")
    assert not Attendance.objects.filter(employee=employee, date=a_weekday).exists()


def test_only_hr_can_revert(employee, head, hr, auth, submit):
    correction = submit()
    workflow.manager_approve(correction, actor=head)
    workflow.hr_approve(correction, actor=hr)
    assert auth(head).post(f"{URL}{correction.id}/revert/", {},
                           format="json").status_code == 403


def test_an_unapplied_request_cannot_be_reverted(employee, head, hr, auth, submit):
    correction = submit()
    assert auth(hr).post(f"{URL}{correction.id}/revert/", {},
                         format="json").status_code == 400


# --------------------------------------------------------------------------
# constraints, scoping, audit
# --------------------------------------------------------------------------
def test_only_one_open_request_per_employee_day(employee, head, submit, a_weekday):
    from django.db.utils import IntegrityError

    submit()
    with pytest.raises(IntegrityError):
        submit()


def test_a_day_can_be_corrected_again_after_a_decision(employee, head, hr, submit,
                                                       a_weekday):
    first = submit()
    workflow.reject(first, actor=head, reason="wrong times")
    second = submit(check_in=(11, 0))
    assert second.pk != first.pk


def test_a_request_must_ask_for_something(employee, auth, a_weekday):
    response = auth(employee).post(URL, {
        "attendance_date": a_weekday.isoformat(), "reason": "please fix"},
        format="json")
    assert response.status_code == 400


def test_a_future_date_is_rejected(employee, auth):
    tomorrow = timezone.localdate() + timedelta(days=1)
    response = auth(employee).post(URL, {
        "attendance_date": tomorrow.isoformat(), "requested_status": "present",
        "reason": "optimistic"}, format="json")
    assert response.status_code == 400


def test_a_requested_time_must_fall_on_the_requested_date(employee, auth, a_weekday, local):
    other_day = a_weekday - timedelta(days=3)
    response = auth(employee).post(URL, {
        "attendance_date": a_weekday.isoformat(),
        "requested_check_in": local(other_day.year, other_day.month, other_day.day,
                                    10, 0).isoformat(),
        "reason": "wrong day"}, format="json")
    assert response.status_code == 400


def test_an_employee_only_sees_their_own_requests(employee, coworker, head, auth, submit):
    submit(user=coworker)
    mine = submit()
    rows = auth(employee).get(URL).data["results"]
    assert {row["id"] for row in rows} == {str(mine.id)}


def test_a_department_head_sees_their_department(employee, outsider, head, auth, submit):
    submit()
    submit(user=outsider)
    rows = auth(head).get(URL).data["results"]
    assert {str(row["employee"]) for row in rows} == {str(employee.id)}


def test_hr_sees_everything(employee, outsider, head, hr, auth, submit):
    submit()
    submit(user=outsider)
    assert auth(hr).get(URL).data["count"] == 2


def test_admins_cannot_raise_a_correction(admin_user, auth, a_weekday):
    response = auth(admin_user).post(URL, {
        "attendance_date": a_weekday.isoformat(), "requested_status": "present",
        "reason": "mine"}, format="json")
    assert response.status_code == 403


@pytest.mark.parametrize("event", [
    "ATTENDANCE_CORRECTION_SUBMIT",
    "ATTENDANCE_CORRECTION_MANAGER_APPROVE",
    "ATTENDANCE_CORRECTION_HR_APPROVE",
])
def test_every_transition_is_audited(employee, head, hr, submit, event):
    correction = submit()
    workflow.manager_approve(correction, actor=head)
    workflow.hr_approve(correction, actor=hr)
    assert AuditLog.objects.filter(changes__event=event).exists()


def test_rejection_and_revert_are_audited(employee, head, hr, submit):
    correction = submit()
    workflow.manager_approve(correction, actor=head)
    workflow.hr_approve(correction, actor=hr)
    workflow.revert(correction, actor=hr, reason="undo")
    assert AuditLog.objects.filter(changes__event="ATTENDANCE_CORRECTION_REVERT").exists()

    second = submit(check_in=(11, 0))
    workflow.reject(second, actor=head, reason="no")
    assert AuditLog.objects.filter(changes__event="ATTENDANCE_CORRECTION_REJECT").exists()


def test_the_queue_counts_endpoint_reports_what_is_waiting(employee, head, hr, auth,
                                                           submit):
    submit()
    counts = auth(head).get(f"{URL}queue-counts/").data
    assert counts["manager_stage"] == 1
    assert auth(hr).get(f"{URL}queue-counts/").data["manager_stage"] == 1
    assert auth(employee).get(f"{URL}queue-counts/").data["mine_open"] == 1


def test_routing_helper_never_returns_the_employee(employee, dept):
    dept.head = employee
    dept.save(update_fields=["head"])
    assert routing.department_head_for(employee) != employee
