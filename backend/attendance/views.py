from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from audit.services import log_action
from config.nepali_dates import to_bs
from users.models import User

from . import config, services
from .models import Attendance
from .policy import resolver as policy_resolver
from .serializers import AttendanceSerializer, ManualAttendanceSerializer
from users.roles import has_org_wide_read as _org_wide_read


class IsHROrAdmin(BasePermission):
    """HR (approver) or Admin may manage all attendance."""
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in (
            User.Roles.APPROVER, User.Roles.ADMIN,
        )


def _fmt(dt):
    return timezone.localtime(dt).strftime("%H:%M") if dt else None


def _office_payload():
    """Configured office geofence for the client, or None when not set up."""
    office = services.office_location()
    if not office:
        return None
    lat, lng, radius = office
    return {
        # The tenant's own office label, not the deployment's.
        "name": services.office_name() or "Office",
        "lat": lat, "lng": lng, "radius_m": radius,
    }


def _office_start_for(employee, day):
    """The employee's effective start time as "HH:MM".

    Falls back to the raw setting only if resolution somehow fails, so the key
    is never absent from the payload the widget reads.
    """
    try:
        policy = policy_resolver.resolve_policy(employee, day)
        shift = policy_resolver.resolve_shift(employee, day, policy)
        start, _grace = policy_resolver.start_time_and_grace(policy, shift)
        return start.strftime("%H:%M")
    except Exception:  # pragma: no cover - defensive; resolution cannot raise today
        return getattr(settings, "ATTENDANCE_OFFICE_START", "10:00")


def _location_source(coords):
    """What the client said, or what the accuracy implies.

    The browser never reports how it obtained a fix, so a client that does
    not volunteer `location_source` leaves it to be inferred from the
    accuracy radius -- labelled as the heuristic it is in
    `services.classify_accuracy`.
    """
    reported = coords.get("source")
    if reported and reported != Attendance.LocationSource.UNKNOWN:
        return reported
    return services.classify_accuracy(coords.get("accuracy"))


def _user_agent(request):
    """The device string, truncated to the column.

    Stored because "was this the same device at both ends of the day" is a
    question that gets asked in disputes, and it is the cheapest possible
    answer. Truncated rather than parsed: a user-agent parser in this model
    would need updating for every new browser, and the raw string answers
    the question that is actually asked.
    """
    return (request.META.get("HTTP_USER_AGENT") or "")[:256]


class CheckInView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        emp = request.user
        today = services.now_local().date()
        if services.is_holiday(today):
            return Response({"detail": f"Today is a holiday ({services.holiday_name(today)}). No check-in required."},
                            status=status.HTTP_400_BAD_REQUEST)
        # Reject a duplicate before touching anything (and before demanding a
        # location, so re-taps read "already checked in", not "location required").
        existing = Attendance.objects.filter(employee=emp, date=today).first()
        if existing and existing.check_in:
            return Response({"detail": f"You already checked in today at {_fmt(existing.check_in)}."},
                            status=status.HTTP_400_BAD_REQUEST)
        # MODE GATE, before anything else is touched. A customer who has
        # chosen biometric-only has decided their staff do not mark their own
        # attendance; serving the button and refusing the submission would be
        # a worse version of the same answer, so the widget hides itself from
        # the same flag (see the `/attendance/today/` payload).
        if not config.app_check_in_allowed():
            return Response(
                {"detail": "Your organization records attendance from "
                           "biometric devices only. Ask your administrator "
                           "if you need to mark attendance from the app.",
                 "attendance_mode": config.attendance_mode()},
                status=status.HTTP_403_FORBIDDEN)
        coords = services.parse_coordinates(request.data)
        # Location gate: refuse (before creating any row) so no check-in is
        # recorded without the location HR/Admin need. Off => optional capture.
        if services.location_required() and not coords:
            return Response(
                {"detail": "Location is required to check in. Turn on location access for "
                           "this site and try again — on a desktop without GPS, check in from "
                           "your phone. (The site must be opened over HTTPS for location to work.)"},
                status=status.HTTP_400_BAD_REQUEST)
        rec, _ = Attendance.objects.get_or_create(
            employee=emp, date=today, defaults={"marked_by": Attendance.MarkedBy.SELF})
        rec.check_in = timezone.now()
        rec.marked_by = Attendance.MarkedBy.SELF
        if coords:
            rec.check_in_lat = coords["lat"]
            rec.check_in_lng = coords["lng"]
            rec.check_in_accuracy = coords.get("accuracy")
            rec.check_in_address = services.reverse_geocode(coords["lat"], coords["lng"])
            rec.check_in_distance_m = services.distance_from_office(coords["lat"], coords["lng"])
            rec.check_in_location_source = _location_source(coords)
        rec.check_in_user_agent = _user_agent(request)
        services.recompute_status(rec)
        rec.save()
        log_action(emp, AuditLog.Action.CREATE, instance=rec,
                   changes={"event": "ATTENDANCE_CHECK_IN", "at": _fmt(rec.check_in)}, request=request)
        return Response(AttendanceSerializer(rec).data, status=status.HTTP_201_CREATED)


class CheckOutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        emp = request.user
        today = services.now_local().date()
        rec = Attendance.objects.filter(employee=emp, date=today).first()
        if not rec or not rec.check_in:
            return Response({"detail": "You must check in before checking out."},
                            status=status.HTTP_400_BAD_REQUEST)
        if rec.check_out:
            return Response({"detail": f"You already checked out today at {_fmt(rec.check_out)}."},
                            status=status.HTTP_400_BAD_REQUEST)
        # No mode gate here, deliberately. If a customer switches to
        # biometric-only at midday, an employee who checked in through the
        # app this morning must still be able to check out -- otherwise the
        # setting change strands an open record and the day reads as if they
        # never went home.
        coords = services.parse_coordinates(request.data)
        # Same location gate as check-in: a check-out must also carry a location.
        if services.location_required() and not coords:
            return Response(
                {"detail": "Location is required to check out. Turn on location access for "
                           "this site and try again — on a desktop without GPS, check out from "
                           "your phone. (The site must be opened over HTTPS for location to work.)"},
                status=status.HTTP_400_BAD_REQUEST)
        rec.check_out = timezone.now()
        if coords:
            rec.check_out_lat = coords["lat"]
            rec.check_out_lng = coords["lng"]
            rec.check_out_accuracy = coords.get("accuracy")
            rec.check_out_address = services.reverse_geocode(coords["lat"], coords["lng"])
            rec.check_out_distance_m = services.distance_from_office(coords["lat"], coords["lng"])
            rec.check_out_location_source = _location_source(coords)
        rec.check_out_user_agent = _user_agent(request)
        services.recompute_status(rec)
        rec.save()
        log_action(emp, AuditLog.Action.UPDATE, instance=rec,
                   changes={"event": "ATTENDANCE_CHECK_OUT",
                            "at": _fmt(rec.check_out),
                            "hours": str(rec.working_hours),
                            "travelled_m": services.travel_distance_m(rec)},
                   request=request)
        return Response(AttendanceSerializer(rec).data)


class TodayView(APIView):
    """Today's status + this-month summary — powers the dashboard widget."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        emp = request.user
        now = services.now_local()
        today = now.date()
        rec = Attendance.objects.filter(employee=emp, date=today).first()
        eff = services.effective_status(emp, today, rec)
        month = services.build_calendar(emp, now.year, now.month)
        return Response({
            "date": today.isoformat(),
            "date_bs": to_bs(today),
            "status": eff,
            "check_in": rec.check_in.isoformat() if rec and rec.check_in else None,
            "check_out": rec.check_out.isoformat() if rec and rec.check_out else None,
            "check_in_local": _fmt(rec.check_in) if rec and rec.check_in else None,
            "check_out_local": _fmt(rec.check_out) if rec and rec.check_out else None,
            "check_in_lat": str(rec.check_in_lat) if rec and rec.check_in_lat is not None else None,
            "check_in_lng": str(rec.check_in_lng) if rec and rec.check_in_lng is not None else None,
            "check_in_accuracy": rec.check_in_accuracy if rec else None,
            "check_in_address": (rec.check_in_address or None) if rec else None,
            "check_out_lat": str(rec.check_out_lat) if rec and rec.check_out_lat is not None else None,
            "check_out_lng": str(rec.check_out_lng) if rec and rec.check_out_lng is not None else None,
            "check_out_accuracy": rec.check_out_accuracy if rec else None,
            "check_out_address": (rec.check_out_address or None) if rec else None,
            "check_in_distance_m": rec.check_in_distance_m if rec else None,
            "check_out_distance_m": rec.check_out_distance_m if rec else None,
            "check_in_within_office": services.is_within_office(rec.check_in_distance_m) if rec else None,
            "check_out_within_office": services.is_within_office(rec.check_out_distance_m) if rec else None,
            "working_hours": str(rec.working_hours) if rec else "0.00",
            # `can_check_in` now carries the MODE as well, so a tenant on
            # biometric-only never shows its staff a button that will be
            # refused. The widget reads this flag; the view enforces it
            # independently -- a hidden button is a courtesy, not a control.
            "can_check_in": bool(not (rec and rec.check_in)
                                 and not services.is_holiday(today)
                                 and config.app_check_in_allowed()),
            # NOT gated on the mode: an employee who checked in through the
            # app this morning must be able to check out even if an
            # administrator switched to biometric-only at lunchtime.
            "can_check_out": bool(rec and rec.check_in and not rec.check_out),
            "attendance_mode": config.attendance_mode(),
            "app_check_in_allowed": config.app_check_in_allowed(),
            "travel_distance_m": services.travel_distance_m(rec) if rec else None,
            "check_in_location_source": (rec.check_in_location_source or None) if rec else None,
            "check_out_location_source": (rec.check_out_location_source or None) if rec else None,
            # The RESOLVED start for this employee (their shift, else their
            # policy, else the setting). Same key, same "HH:MM" shape the
            # dashboard widget already renders — no frontend change needed.
            # This supersedes the flat ATTENDANCE_OFFICE_START the geofence work
            # used: same key, but correct for staff on a non-default shift.
            "office_start": _office_start_for(emp, today),
            "grace_minutes": policy_resolver.resolve_policy(emp, today).grace_minutes,
            "is_wfh": bool(rec and rec.is_wfh),
            "overtime_hours": str(rec.overtime_hours) if rec else "0.00",
            # Geofence config for the dashboard's live map: lets the browser show
            # the office pin and a live distance without a round trip per fix.
            "office": _office_payload(),
            # Per tenant now, like the office pin beside it.
            "max_accuracy_m": services.max_accuracy_m(),
            "month_summary": month["summary"],
        })


class MyCalendarView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        now = services.now_local()
        year = int(request.query_params.get("year") or now.year)
        month = int(request.query_params.get("month") or now.month)
        return Response(services.build_calendar(request.user, year, month))


class AttendanceListView(APIView):
    """Role-scoped attendance list with filters (Employee=own, Dept Head=dept, HR/Admin=all)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        role = request.user.role
        qs = Attendance.objects.select_related("employee", "employee__department_ref")
        if _org_wide_read(request.user):
            pass  # all - HR, Admin, and the Board read-only
        elif role == User.Roles.CHECKER and request.user.department_ref_id:
            qs = qs.filter(employee__department_ref_id=request.user.department_ref_id)
        else:
            # Including a department head with NO department. Filtering on
            # their (null) department_ref_id became `IS NULL` and handed them
            # the attendance of every employee not yet assigned a department.
            qs = qs.filter(employee=request.user)

        p = request.query_params
        if p.get("date_from"):
            qs = qs.filter(date__gte=p["date_from"])
        if p.get("date_to"):
            qs = qs.filter(date__lte=p["date_to"])
        if p.get("department"):
            qs = qs.filter(employee__department_ref_id=p["department"])
        if p.get("employee"):
            qs = qs.filter(employee_id=p["employee"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        # Global search (Ctrl+K): by person, within what this caller may see.
        term = (p.get("search") or "").strip()
        if term:
            from django.db.models import Q

            qs = qs.filter(Q(employee__first_name__icontains=term)
                           | Q(employee__last_name__icontains=term)
                           | Q(employee__employee_id__icontains=term))
        qs = qs.order_by("-date", "employee__first_name")[:500]
        return Response(AttendanceSerializer(qs, many=True).data)


class ManualAttendanceView(APIView):
    """HR/Admin manual add/edit (marked_by = HR), with remarks."""
    permission_classes = [IsAuthenticated, IsHROrAdmin]

    def post(self, request):
        ser = ManualAttendanceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        try:
            emp = User.objects.get(id=d["employee"])
        except User.DoesNotExist:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        rec, _ = Attendance.objects.get_or_create(employee=emp, date=d["date"])
        rec.status = d["status"]
        rec.check_in = d.get("check_in") or rec.check_in
        rec.check_out = d.get("check_out") or rec.check_out
        rec.remarks = d.get("remarks", "")
        rec.marked_by = Attendance.MarkedBy.HR
        if rec.check_in and rec.check_out:
            rec.working_hours = services.compute_working_hours(rec.check_in, rec.check_out)
        rec.save()
        log_action(request.user, AuditLog.Action.UPDATE, instance=rec,
                   changes={"event": "ATTENDANCE_MANUAL", "status": rec.status,
                            "employee": str(emp.id)}, request=request)
        return Response(AttendanceSerializer(rec).data, status=status.HTTP_200_OK)
