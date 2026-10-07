"""Work from home.

The rule under test, approved in the Phase 8 design:

    approved WFH request  +  browser check-in  =  WORK_FROM_HOME
    approved WFH request  alone                =  NOT attendance

Approval on its own must never manufacture attendance for someone who never
started work.
"""
from datetime import timedelta

import pytest

from attendance.models import Attendance, WFHRequest
from attendance.services import recompute_status

pytestmark = pytest.mark.django_db

WFH_URL = "/api/v1/attendance/wfh/"


@pytest.fixture
def approved_wfh(employee, hr, a_weekday):
    return WFHRequest.objects.create(
        user=employee, start_date=a_weekday, end_date=a_weekday,
        reason="Fibre cut at the office", status=WFHRequest.Status.APPROVED,
        reviewed_by=hr)


def check_in(employee, day, local, source=Attendance.Source.BROWSER, hours=(10, 0, 18, 0)):
    record = Attendance(
        employee=employee, date=day, source=source,
        check_in=local(day.year, day.month, day.day, hours[0], hours[1]),
        check_out=local(day.year, day.month, day.day, hours[2], hours[3]))
    recompute_status(record)
    record.save()
    return record


# --------------------------------------------------------------------------
# the status rule
# --------------------------------------------------------------------------
def test_approved_wfh_without_a_check_in_is_not_attendance(employee, approved_wfh,
                                                           a_weekday):
    """The required 'WFH approved without checkin' case."""
    from attendance.services import effective_status

    assert not Attendance.objects.filter(employee=employee, date=a_weekday).exists()
    assert effective_status(employee, a_weekday) == Attendance.Status.ABSENT


def test_approved_wfh_with_a_check_in_is_work_from_home(employee, approved_wfh,
                                                        a_weekday, local):
    """The required 'WFH approved with checkin' case."""
    record = check_in(employee, a_weekday, local)
    assert record.status == Attendance.Status.WORK_FROM_HOME
    assert record.is_wfh is True


def test_a_check_in_without_an_approved_request_is_ordinary_attendance(employee,
                                                                       a_weekday, local):
    record = check_in(employee, a_weekday, local)
    assert record.status == Attendance.Status.PRESENT
    assert record.is_wfh is False


def test_a_pending_request_does_not_make_a_day_wfh(employee, a_weekday, local):
    WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday,
                              status=WFHRequest.Status.PENDING)
    assert check_in(employee, a_weekday, local).status == Attendance.Status.PRESENT


def test_a_rejected_request_does_not_make_a_day_wfh(employee, hr, a_weekday, local):
    WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday,
                              status=WFHRequest.Status.REJECTED, reviewed_by=hr)
    assert check_in(employee, a_weekday, local).status == Attendance.Status.PRESENT


def test_a_biometric_punch_is_never_wfh(employee, approved_wfh, a_weekday, local):
    """A device punch means the employee was physically at a terminal."""
    record = check_in(employee, a_weekday, local, source=Attendance.Source.BIOMETRIC)
    assert record.status == Attendance.Status.PRESENT
    assert record.is_wfh is True, "the approval is still recorded on the row"


def test_a_late_wfh_day_is_still_work_from_home(employee, approved_wfh, a_weekday, local):
    # 12:30 is late under both the grace rules and the NIF arrival rules, so
    # this assertion does not depend on which side of the cutover it lands on.
    record = check_in(employee, a_weekday, local, hours=(12, 30, 18, 30))
    assert record.status == Attendance.Status.WORK_FROM_HOME
    assert record.late_minutes > 0, "lateness is still recorded"


def test_a_short_wfh_day_is_still_a_half_day(employee, approved_wfh, a_weekday, local):
    record = check_in(employee, a_weekday, local, hours=(10, 0, 13, 0))
    assert record.status == Attendance.Status.HALF_DAY


def test_wfh_covers_every_day_in_the_range(employee, hr, a_weekday, local):
    WFHRequest.objects.create(
        user=employee, start_date=a_weekday - timedelta(days=2), end_date=a_weekday,
        status=WFHRequest.Status.APPROVED, reviewed_by=hr)
    for offset in range(3):
        day = a_weekday - timedelta(days=offset)
        if day.weekday() == 5:
            continue
        assert check_in(employee, day, local).status == Attendance.Status.WORK_FROM_HOME


# --------------------------------------------------------------------------
# the request workflow
# --------------------------------------------------------------------------
def test_an_employee_can_raise_a_request(employee, auth, a_weekday):
    response = auth(employee).post(WFH_URL, {
        "start_date": a_weekday.isoformat(), "end_date": a_weekday.isoformat(),
        "reason": "Home internet is faster"}, format="json")
    assert response.status_code == 201
    assert response.data["status"] == WFHRequest.Status.PENDING
    assert str(response.data["user"]) == str(employee.id)


def test_an_employee_cannot_self_approve(employee, auth, a_weekday):
    wfh = WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)
    assert auth(employee).post(f"{WFH_URL}{wfh.id}/approve/", {},
                               format="json").status_code == 403


def test_hr_can_approve(employee, hr, auth, a_weekday):
    wfh = WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)
    response = auth(hr).post(f"{WFH_URL}{wfh.id}/approve/", {"note": "ok"}, format="json")
    assert response.status_code == 200
    wfh.refresh_from_db()
    assert wfh.status == WFHRequest.Status.APPROVED
    assert wfh.reviewed_by_id == hr.id
    assert wfh.reviewed_at is not None


def test_hr_can_reject(employee, hr, auth, a_weekday):
    wfh = WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)
    assert auth(hr).post(f"{WFH_URL}{wfh.id}/reject/", {"note": "come in"},
                         format="json").status_code == 200
    wfh.refresh_from_db()
    assert wfh.status == WFHRequest.Status.REJECTED


def test_a_request_cannot_be_reviewed_twice(employee, hr, auth, a_weekday):
    wfh = WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)
    auth(hr).post(f"{WFH_URL}{wfh.id}/approve/", {}, format="json")
    assert auth(hr).post(f"{WFH_URL}{wfh.id}/reject/", {}, format="json").status_code == 400


def test_approving_after_the_check_in_flips_the_day(employee, hr, auth, a_weekday, local):
    """The employee checks in first, HR approves afterwards — the day must
    become WFH without anyone re-running derivation by hand."""
    record = check_in(employee, a_weekday, local)
    assert record.status == Attendance.Status.PRESENT

    wfh = WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)
    auth(hr).post(f"{WFH_URL}{wfh.id}/approve/", {}, format="json")

    record.refresh_from_db()
    assert record.status == Attendance.Status.WORK_FROM_HOME


def test_an_hr_corrected_day_is_not_flipped_by_a_wfh_approval(employee, hr, auth,
                                                              a_weekday, local):
    record = check_in(employee, a_weekday, local)
    Attendance.objects.filter(pk=record.pk).update(
        marked_by=Attendance.MarkedBy.HR, status=Attendance.Status.PRESENT)

    wfh = WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)
    auth(hr).post(f"{WFH_URL}{wfh.id}/approve/", {}, format="json")

    record.refresh_from_db()
    assert record.status == Attendance.Status.PRESENT, "HR corrections outrank everything"


def test_an_employee_only_sees_their_own_requests(employee, coworker, auth, a_weekday):
    WFHRequest.objects.create(user=coworker, start_date=a_weekday, end_date=a_weekday)
    mine = WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)

    rows = auth(employee).get(WFH_URL).data["results"]
    assert {str(row["id"]) for row in rows} == {str(mine.id)}


def test_hr_sees_every_request(employee, coworker, hr, auth, a_weekday):
    WFHRequest.objects.create(user=coworker, start_date=a_weekday, end_date=a_weekday)
    WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)
    assert auth(hr).get(WFH_URL).data["count"] == 2


def test_a_department_head_sees_their_department(employee, outsider, dept_head, auth,
                                                 a_weekday):
    WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)
    WFHRequest.objects.create(user=outsider, start_date=a_weekday, end_date=a_weekday)
    rows = auth(dept_head).get(WFH_URL).data["results"]
    assert {str(row["user"]) for row in rows} == {str(employee.id)}


def test_an_employee_can_cancel_their_pending_request(employee, auth, a_weekday):
    wfh = WFHRequest.objects.create(user=employee, start_date=a_weekday, end_date=a_weekday)
    assert auth(employee).post(f"{WFH_URL}{wfh.id}/cancel/", {},
                               format="json").status_code == 200
    wfh.refresh_from_db()
    assert wfh.status == WFHRequest.Status.CANCELLED


def test_end_date_cannot_precede_start_date(employee, auth, a_weekday):
    response = auth(employee).post(WFH_URL, {
        "start_date": a_weekday.isoformat(),
        "end_date": (a_weekday - timedelta(days=1)).isoformat()}, format="json")
    assert response.status_code == 400
