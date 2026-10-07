"""Attendance report exports (Admin/HR only): single weekly/monthly + bulk PDF/ZIP."""
import io
import zipfile
from datetime import datetime

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from config.nepali_dates import to_bs
from documents.pdf import logo_data_uri, render_pdf
from tenancy.scoping import active_organization
from users.models import User

from . import reports
from .views import IsHROrAdmin


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _get_emp(pk):
    try:
        return User.objects.select_related("department_ref").get(pk=pk)
    except (User.DoesNotExist, ValueError, Exception):
        raise NotFound("Employee not found.")


def _base_ctx(title, period, generated_by):
    """The letterhead for an attendance report.

    `org` WAS `settings.ORG_INFO` — the PLATFORM's name, address, telephone
    number and website — so every tenant's attendance and leave reports went
    out under the platform operator's letterhead while the logo beside it was
    already the tenant's. Phase S9 fixed exactly this for memos, minutes and
    circulars through `org_letterhead`; attendance reports were a separate
    renderer and were missed.

    The brand migration is what surfaced it: the wrong name silently became
    "Buddhi Labs" on every customer's report.
    """
    from documents.pdf import org_letterhead

    now = timezone.localtime(timezone.now())
    return {
        "logo": logo_data_uri(),
        "org": org_letterhead(active_organization(required=False)),
        "powered_by": getattr(settings, "PLATFORM_POWERED_BY",
                              "Powered by Buddhi Labs"),
        "report_title": title,
        "period": period,
        "generated_ad": now.strftime("%Y-%m-%d %H:%M"),
        "generated_bs": to_bs(now.date()),
        "generated_by": generated_by,
    }


def _pdf(pdf_bytes, filename):
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def _actor(request):
    return request.user.get_full_name() or request.user.username


class EmployeeWeeklyReportView(APIView):
    permission_classes = [IsAuthenticated, IsHROrAdmin]

    def get(self, request, pk):
        emp = _get_emp(pk)
        anchor = _parse_date(request.query_params.get("week")) or timezone.localtime(timezone.now()).date()
        start, end = reports.week_range(anchor)
        report = reports.build_employee_report(emp, start, end)
        ctx = _base_ctx("Weekly Attendance Report", report["period"], _actor(request))
        ctx["report"] = report
        pdf = render_pdf("pdf/attendance_report.html", ctx)
        return _pdf(pdf, f"attendance_{emp.employee_id or emp.id}_weekly_{start}.pdf")


class EmployeeMonthlyReportView(APIView):
    permission_classes = [IsAuthenticated, IsHROrAdmin]

    def get(self, request, pk):
        emp = _get_emp(pk)
        now = timezone.localtime(timezone.now())
        year = int(request.query_params.get("year") or now.year)
        month = int(request.query_params.get("month") or now.month)
        start, end = reports.month_range(year, month)
        report = reports.build_employee_report(emp, start, end)
        ctx = _base_ctx("Monthly Attendance Report", report["period"], _actor(request))
        ctx["report"] = report
        pdf = render_pdf("pdf/attendance_report.html", ctx)
        return _pdf(pdf, f"attendance_{emp.employee_id or emp.id}_{year}_{month:02d}.pdf")


class AllReportView(APIView):
    """Bulk: combined PDF (default) or ZIP of individual PDFs (format=zip)."""
    permission_classes = [IsAuthenticated, IsHROrAdmin]

    def get(self, request):
        p = request.query_params
        period = (p.get("period") or "monthly").lower()
        now = timezone.localtime(timezone.now()).date()
        start = _parse_date(p.get("start"))
        end = _parse_date(p.get("end"))
        if not (start and end):
            start, end = (reports.week_range(now) if period == "weekly"
                          else reports.month_range(now.year, now.month))

        emps = User.objects.filter(is_active=True).select_related("department_ref")
        if p.get("department"):
            emps = emps.filter(department_ref_id=p["department"])
        emps = emps.order_by("department_ref__name", "first_name", "last_name")
        emps = list(emps)

        title = ("Weekly" if period == "weekly" else "Monthly") + " Attendance Report"

        # NB: use `output` (not `format`) — `format` is reserved by DRF content negotiation.
        if (p.get("output") or "pdf").lower() == "zip":
            data = reports.build_bulk(emps, start, end)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for r in data["reports"]:
                    ctx = _base_ctx(title, r["period"], _actor(request))
                    ctx["report"] = r
                    pdf = render_pdf("pdf/attendance_report.html", ctx)
                    safe = f"{r['employee']['employee_id']}_{r['employee']['name']}".replace(" ", "_")
                    z.writestr(f"{safe}.pdf", pdf)
            resp = HttpResponse(buf.getvalue(), content_type="application/zip")
            resp["Content-Disposition"] = f'attachment; filename="attendance_{period}_{start}.zip"'
            return resp

        data = reports.build_bulk(emps, start, end)
        ctx = _base_ctx(title, data["period"], _actor(request))
        ctx.update(data)
        pdf = render_pdf("pdf/attendance_bulk.html", ctx)
        return _pdf(pdf, f"attendance_all_{period}_{start}.pdf")


class ReportOptionsView(APIView):
    """
    GET /api/v1/attendance/report/options/
    HR/Admin-safe read-only lookup for the Attendance Reports page: the employee
    list + department list needed to populate its dropdowns. Scoped to the SAME
    roles as the reports themselves (IsHROrAdmin) so HR isn't forced through the
    admin-only user/department CRUD endpoints.
    """
    permission_classes = [IsAuthenticated, IsHROrAdmin]

    def get(self, request):
        from leaves.models import Department
        employees = (
            User.objects.filter(is_active=True)
            .order_by("first_name", "last_name", "username")
        )
        departments = Department.objects.filter(is_active=True).order_by("name")
        return Response({
            "employees": [{
                "id": str(u.id),
                "full_name": u.get_full_name() or u.username,
                "employee_id": u.employee_id,
            } for u in employees],
            "departments": [{
                "id": str(d.id), "name": d.name, "code": d.code,
            } for d in departments],
        })
