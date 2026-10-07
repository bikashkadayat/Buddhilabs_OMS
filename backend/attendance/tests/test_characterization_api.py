"""Characterization tests for the attendance HTTP surface.

Asserts today's behaviour of check-in/out, the dashboard payload, the calendar,
the role-scoped list and HR manual entry — the contract the React app depends
on. Phase 5 must leave every one of these green without edits.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from attendance.models import Attendance
from leaves.models import Leave, LeaveBalance

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _location_capture_optional(settings):
    """Relax the geofence's mandatory-location gate for this module.

    ``ATTENDANCE_REQUIRE_LOCATION`` defaults ON, so a check-in with no
    coordinates is refused with 400. Nothing in this module is about location —
    these tests predate the geofence — and making each one post coordinates
    would test the geofence by accident and obscure what they actually assert.

    The gate itself is covered where it belongs, in ``test_geofence.py``, which
    uses this same override for its own non-location cases.
    """
    settings.ATTENDANCE_REQUIRE_LOCATION = False

CHECK_IN = "/api/v1/attendance/check-in/"
CHECK_OUT = "/api/v1/attendance/check-out/"
TODAY = "/api/v1/attendance/today/"
ME = "/api/v1/attendance/me/"
LIST = "/api/v1/attendance/"
MANUAL = "/api/v1/attendance/manual/"


@pytest.fixture(autouse=True)
def _no_outbound_geocoding(settings):
    """Check-in makes a live Nominatim call; keep tests off the network."""
    settings.ATTENDANCE_REVERSE_GEOCODE = False


@pytest.fixture
def not_a_holiday(settings, db):
    """Skip when today is Saturday/a holiday — check-in is blocked then."""
    from attendance import services
    if services.is_holiday(timezone.localdate()):
        pytest.skip("today is a holiday; check-in is intentionally blocked")


# --------------------------------------------------------------------------
# Check-in / check-out
# --------------------------------------------------------------------------

def test_check_in_creates_todays_record(auth, employee, not_a_holiday):
    resp = auth(employee).post(CHECK_IN, {}, format="json")
    assert resp.status_code == 201
    rec = Attendance.objects.get(employee=employee, date=timezone.localdate())
    assert rec.check_in is not None
    assert rec.marked_by == Attendance.MarkedBy.SELF


def test_double_check_in_is_rejected(auth, employee, not_a_holiday):
    client = auth(employee)
    client.post(CHECK_IN, {}, format="json")
    resp = client.post(CHECK_IN, {}, format="json")
    assert resp.status_code == 400
    assert "already checked in" in resp.data["detail"]


def test_check_in_stores_geolocation_when_supplied(auth, employee, not_a_holiday):
    auth(employee).post(CHECK_IN, {"latitude": "27.7172", "longitude": "85.3240",
                                   "accuracy": 12.5}, format="json")
    rec = Attendance.objects.get(employee=employee, date=timezone.localdate())
    assert str(rec.check_in_lat) == "27.717200"
    assert rec.check_in_accuracy == 12.5


def test_check_in_succeeds_without_geolocation(auth, employee, not_a_holiday):
    """Location is optional — a denied permission must not block attendance."""
    assert auth(employee).post(CHECK_IN, {}, format="json").status_code == 201
    rec = Attendance.objects.get(employee=employee, date=timezone.localdate())
    assert rec.check_in_lat is None


def test_check_out_requires_a_check_in_first(auth, employee):
    resp = auth(employee).post(CHECK_OUT, {}, format="json")
    assert resp.status_code == 400
    assert "must check in" in resp.data["detail"]


def test_check_out_sets_hours_and_status(auth, employee, not_a_holiday):
    client = auth(employee)
    client.post(CHECK_IN, {}, format="json")
    resp = client.post(CHECK_OUT, {}, format="json")
    assert resp.status_code == 200
    rec = Attendance.objects.get(employee=employee, date=timezone.localdate())
    assert rec.check_out is not None
    assert rec.status in (Attendance.Status.PRESENT, Attendance.Status.LATE,
                          Attendance.Status.HALF_DAY)


def test_double_check_out_is_rejected(auth, employee, not_a_holiday):
    client = auth(employee)
    client.post(CHECK_IN, {}, format="json")
    client.post(CHECK_OUT, {}, format="json")
    assert client.post(CHECK_OUT, {}, format="json").status_code == 400


def test_check_in_and_out_require_authentication(api):
    assert api.post(CHECK_IN, {}, format="json").status_code == 401
    assert api.post(CHECK_OUT, {}, format="json").status_code == 401


# --------------------------------------------------------------------------
# Dashboard payload — the widget's contract
# --------------------------------------------------------------------------

def test_today_payload_shape(auth, employee):
    data = auth(employee).get(TODAY).data
    for key in ("date", "date_bs", "status", "check_in", "check_out", "check_in_local",
                "check_out_local", "working_hours", "can_check_in", "can_check_out",
                "office_start", "month_summary"):
        assert key in data, f"widget depends on '{key}'"


def test_can_check_in_flips_after_checking_in(auth, employee, not_a_holiday):
    client = auth(employee)
    assert client.get(TODAY).data["can_check_in"] is True
    client.post(CHECK_IN, {}, format="json")
    data = client.get(TODAY).data
    assert data["can_check_in"] is False
    assert data["can_check_out"] is True


def test_calendar_endpoint_returns_days_and_summary(auth, employee):
    today = timezone.localdate()
    data = auth(employee).get(f"{ME}?year={today.year}&month={today.month}").data
    assert data["year"] == today.year
    assert isinstance(data["days"], list)
    assert isinstance(data["summary"], dict)


# --------------------------------------------------------------------------
# Role-scoped list
# --------------------------------------------------------------------------

def test_employee_sees_only_their_own_records(auth, employee, coworker, make_attendance,
                                              a_weekday):
    make_attendance(a_weekday, employee=employee)
    make_attendance(a_weekday, employee=coworker)
    rows = auth(employee).get(LIST).data
    assert {str(r["employee"]) for r in rows} == {str(employee.id)}


def test_department_head_sees_their_department(auth, dept_head, employee, outsider,
                                               make_attendance, a_weekday):
    make_attendance(a_weekday, employee=employee)
    make_attendance(a_weekday, employee=outsider)
    ids = {str(r["employee"]) for r in auth(dept_head).get(LIST).data}
    assert str(employee.id) in ids
    assert str(outsider.id) not in ids


def test_hr_and_admin_see_everything(auth, hr, admin_user, employee, outsider,
                                     make_attendance, a_weekday):
    make_attendance(a_weekday, employee=employee)
    make_attendance(a_weekday, employee=outsider)
    for manager in (hr, admin_user):
        ids = {str(r["employee"]) for r in auth(manager).get(LIST).data}
        assert {str(employee.id), str(outsider.id)} <= ids


def test_list_filters(auth, hr, employee, make_attendance, a_weekday):
    make_attendance(a_weekday, employee=employee, status=Attendance.Status.PRESENT)
    make_attendance(a_weekday - timedelta(days=1), employee=employee,
                    status=Attendance.Status.ABSENT)
    client = auth(hr)
    assert len(client.get(f"{LIST}?date_from={a_weekday}").data) == 1
    assert len(client.get(f"{LIST}?status=absent").data) == 1
    assert len(client.get(f"{LIST}?employee={employee.id}").data) == 2


def test_list_serializer_exposes_the_expected_fields(auth, hr, employee,
                                                     make_attendance, a_weekday):
    make_attendance(a_weekday, employee=employee)
    row = auth(hr).get(LIST).data[0]
    for key in ("id", "employee", "employee_name", "employee_id", "department_name",
                "date", "date_bs", "check_in", "check_out", "status", "status_display",
                "working_hours", "remarks", "marked_by"):
        assert key in row


# --------------------------------------------------------------------------
# HR manual entry
# --------------------------------------------------------------------------

def test_manual_entry_is_hr_or_admin_only(auth, employee, dept_head, a_weekday):
    payload = {"employee": str(employee.id), "date": a_weekday.isoformat(), "status": "present"}
    assert auth(employee).post(MANUAL, payload, format="json").status_code == 403
    assert auth(dept_head).post(MANUAL, payload, format="json").status_code == 403


def test_hr_can_create_a_manual_record(auth, hr, employee, a_weekday):
    resp = auth(hr).post(MANUAL, {
        "employee": str(employee.id), "date": a_weekday.isoformat(),
        "status": "half_day", "remarks": "Left early — approved",
    }, format="json")
    assert resp.status_code == 200
    rec = Attendance.objects.get(employee=employee, date=a_weekday)
    assert rec.status == Attendance.Status.HALF_DAY
    assert rec.marked_by == Attendance.MarkedBy.HR
    assert rec.remarks == "Left early — approved"


def test_manual_entry_updates_an_existing_row(auth, hr, employee, make_attendance, a_weekday):
    make_attendance(a_weekday, employee=employee, status=Attendance.Status.ABSENT)
    auth(hr).post(MANUAL, {"employee": str(employee.id), "date": a_weekday.isoformat(),
                           "status": "present"}, format="json")
    assert Attendance.objects.filter(employee=employee, date=a_weekday).count() == 1
    assert Attendance.objects.get(employee=employee, date=a_weekday).status == "present"


def test_manual_entry_computes_hours_when_both_times_given(auth, hr, employee, a_weekday, local):
    auth(hr).post(MANUAL, {
        "employee": str(employee.id), "date": a_weekday.isoformat(), "status": "present",
        "check_in": local(a_weekday.year, a_weekday.month, a_weekday.day, 10, 0).isoformat(),
        "check_out": local(a_weekday.year, a_weekday.month, a_weekday.day, 18, 0).isoformat(),
    }, format="json")
    rec = Attendance.objects.get(employee=employee, date=a_weekday)
    assert str(rec.working_hours) == "8.00"


def test_manual_entry_for_unknown_employee_is_404(auth, hr, a_weekday):
    import uuid
    resp = auth(hr).post(MANUAL, {"employee": str(uuid.uuid4()),
                                  "date": a_weekday.isoformat(), "status": "present"},
                         format="json")
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Model invariants
# --------------------------------------------------------------------------

def test_one_attendance_row_per_employee_per_day(employee, make_attendance, a_weekday):
    from django.db import IntegrityError, transaction
    make_attendance(a_weekday, employee=employee)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_attendance(a_weekday, employee=employee)


def test_leave_day_shows_on_leave_in_the_calendar(employee, a_weekday, auth):
    Leave.objects.create(
        user=employee, leave_type=LeaveBalance.LeaveType.ANNUAL,
        start_date=a_weekday, end_date=a_weekday, reason="test",
        status=Leave.Status.APPROVED,
    )
    data = auth(employee).get(f"{ME}?year={a_weekday.year}&month={a_weekday.month}").data
    entry = next(d for d in data["days"] if d["date"] == a_weekday.isoformat())
    assert entry["status"] == Attendance.Status.ON_LEAVE
