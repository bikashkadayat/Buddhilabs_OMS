"""
HR/Admin API for enterprise leave management (Phase 7).

Thin views delegating to leaves.services; every mutating action is audit-logged.
All endpoints require admin via users.admin_views.IsAdminOrSuperuser.
"""
import csv
import io

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from audit.services import log_action
from users.admin_views import IsAdminOrSuperuser
from . import services
from .filters import UserFilter
from .models import (
    CalendarEvent, Department, EnterpriseLeaveBalance, Holiday, Leave,
    LeavePolicy, LeaveType, MonthlyLeaveSummary,
)
from .serializers import (
    EnterpriseLeaveBalanceSerializer, LeaveSerializer, MonthlyLeaveSummarySerializer,
)
from .admin_serializers import (
    AdminCalendarEventSerializer, AdminDepartmentSerializer, AdminHolidaySerializer,
    AdminLeavePolicySerializer, AdminLeaveTypeSerializer, BalanceAdjustmentSerializer,
    BulkLeaveActionSerializer, EmployeeSummarySerializer,
)

User = get_user_model()


class AuditedModelViewSet(viewsets.ModelViewSet):
    """ModelViewSet that audit-logs create/update/destroy. Admin-only."""
    permission_classes = [IsAdminOrSuperuser]

    def perform_create(self, serializer):
        serializer.save()
        log_action(self.request.user, AuditLog.Action.CREATE, instance=serializer.instance, request=self.request)

    def perform_update(self, serializer):
        serializer.save()
        log_action(self.request.user, AuditLog.Action.UPDATE, instance=serializer.instance, request=self.request)

    def perform_destroy(self, instance):
        log_action(self.request.user, AuditLog.Action.DELETE, instance=instance, request=self.request)
        instance.delete()


# ---------------------------------------------------------------------------
# 1. Employees
# ---------------------------------------------------------------------------
class AdminEmployeeLeaveViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAdminOrSuperuser]
    serializer_class = EmployeeSummarySerializer
    filterset_class = UserFilter
    search_fields = ['first_name', 'last_name', 'email', 'username']
    ordering_fields = ['username', 'email', 'role', 'date_joined']

    def get_queryset(self):
        return User.objects.select_related('department_ref').all().order_by('username')

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        year = self.request.query_params.get('year')
        ctx['year'] = int(year) if year else timezone.now().year
        return ctx

    def retrieve(self, request, *args, **kwargs):
        employee = self.get_object()
        year = int(request.query_params.get('year') or timezone.now().year)
        balances = EnterpriseLeaveBalance.objects.filter(user=employee, year=year).select_related('leave_type')
        leaves = Leave.objects.filter(user=employee, is_deleted=False).order_by('-created_at')[:50]
        monthly = MonthlyLeaveSummary.objects.filter(user=employee, year=year).order_by('month')
        return Response({
            'employee': EmployeeSummarySerializer(employee, context={'year': year}).data,
            'year': year,
            'balances': EnterpriseLeaveBalanceSerializer(balances, many=True).data,
            'applications': LeaveSerializer(leaves, many=True).data,
            'monthly_summaries': MonthlyLeaveSummarySerializer(monthly, many=True).data,
        })

    @action(detail=True, methods=['post'], url_path='adjust-balance')
    def adjust_balance(self, request, pk=None):
        employee = self.get_object()
        serializer = BalanceAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        leave_type = LeaveType.objects.get(code=data['leave_type'])
        balance = services.adjust_leave_balance(
            employee, leave_type, data['year'], data['delta'],
            actor=request.user, reason=data['reason'], request=request,
        )
        return Response(EnterpriseLeaveBalanceSerializer(balance).data)


# ---------------------------------------------------------------------------
# 2. Policies
# ---------------------------------------------------------------------------
class AdminPolicyViewSet(AuditedModelViewSet):
    serializer_class = AdminLeavePolicySerializer
    filterset_fields = ['leave_type', 'department', 'role']
    ordering_fields = ['effective_from', 'days_per_year']

    def get_queryset(self):
        return LeavePolicy.objects.select_related('leave_type', 'department').all()

    def _overlaps(self, data, exclude_id=None):
        """Existing effective policies for the same type/department/role whose
        date range intersects the new one."""
        qs = LeavePolicy.objects.filter(
            leave_type=data['leave_type'],
            department=data.get('department'),
            role=data.get('role') or None,
        )
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)
        new_from = data['effective_from']
        new_until = data.get('effective_until')
        overlaps = []
        for p in qs:
            p_until = p.effective_until
            if (p_until is None or p_until >= new_from) and (new_until is None or p.effective_from <= new_until):
                overlaps.append(str(p.id))
        return overlaps

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        warning = self._overlaps(serializer.validated_data)
        instance = serializer.save(created_by=request.user)
        log_action(request.user, AuditLog.Action.CREATE, instance=instance, request=request)
        body = serializer.data
        if warning:
            body = {**body, 'warning': f'Overlaps {len(warning)} existing policy(ies).', 'overlaps': warning}
        return Response(body, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        """Never hard-delete a policy - deprecate it by ending it today."""
        policy = self.get_object()
        policy.effective_until = timezone.now().date()
        policy.save(update_fields=['effective_until'])
        services.audit_log(request.user, 'LEAVE_POLICY_DEPRECATED', target=policy,
                           metadata={'effective_until': str(policy.effective_until)}, request=request)
        return Response(self.get_serializer(policy).data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# 3. Holidays (+ CSV bulk import)
# ---------------------------------------------------------------------------
class AdminHolidayViewSet(AuditedModelViewSet):
    serializer_class = AdminHolidaySerializer
    filterset_fields = ['holiday_type', 'is_active']
    ordering_fields = ['date', 'name']

    def get_queryset(self):
        qs = Holiday.objects.all()
        year = self.request.query_params.get('year')
        if year:
            qs = qs.filter(date__year=year)
        return qs

    @action(detail=False, methods=['post'], url_path='bulk-import')
    def bulk_import(self, request):
        upload = request.FILES.get('file')
        if upload is None:
            return Response({'detail': 'Upload a CSV file under "file".'}, status=status.HTTP_400_BAD_REQUEST)

        decoded = io.StringIO(upload.read().decode('utf-8-sig'))
        reader = csv.DictReader(decoded)
        created, updated, errors = 0, 0, []
        with transaction.atomic():
            for i, row in enumerate(reader, start=2):  # line 1 is the header
                date_val = (row.get('date') or '').strip()
                name = (row.get('name') or '').strip()
                if not date_val or not name:
                    errors.append({'line': i, 'error': 'date and name are required'})
                    continue
                try:
                    _, was_created = Holiday.objects.update_or_create(
                        date=date_val,
                        defaults={
                            'name': name,
                            'holiday_type': (row.get('type') or 'public').strip() or 'public',
                            'description': (row.get('description') or '').strip(),
                            'is_active': True,
                        },
                    )
                    created += int(was_created)
                    updated += int(not was_created)
                except Exception as exc:  # noqa: BLE001 - report bad rows, don't abort
                    errors.append({'line': i, 'error': str(exc)})

        services.audit_log(request.user, 'HOLIDAY_BULK_IMPORT', target=None,
                           metadata={'created': created, 'updated': updated, 'errors': len(errors)},
                           request=request)
        return Response({'created': created, 'updated': updated, 'errors': errors},
                        status=status.HTTP_200_OK if not errors else status.HTTP_207_MULTI_STATUS)


# ---------------------------------------------------------------------------
# 3b. Calendar events (Nepali-calendar festivals / observances / jayantis)
# ---------------------------------------------------------------------------
class AdminCalendarEventViewSet(AuditedModelViewSet):
    """
    CRUD for the display-only entries shown on the Nepali calendar.

    Separate from AdminHolidayViewSet on purpose: a Holiday is one-per-day and
    makes the day non-working, while a CalendarEvent is display-only and several
    may share a date (a festival plus a jayanti plus an observance). Employees
    read these through the calendar's public /leaves/calendar-events/ endpoint;
    this admin route is the only write path.
    """
    serializer_class = AdminCalendarEventSerializer
    filterset_fields = ['event_type', 'is_public_holiday', 'is_active']
    search_fields = ['name', 'name_np', 'tithi']
    ordering_fields = ['date', 'name']

    def get_queryset(self):
        qs = CalendarEvent.objects.all().order_by('date', '-is_public_holiday', 'name')
        params = self.request.query_params
        year = params.get('year')
        if year:
            qs = qs.filter(date__year=year)
        month = params.get('month')
        if month:
            qs = qs.filter(date__month=month)
        start, end = params.get('start'), params.get('end')
        if start:
            qs = qs.filter(date__gte=start)
        if end:
            qs = qs.filter(date__lte=end)
        return qs


# ---------------------------------------------------------------------------
# 4. Departments
# ---------------------------------------------------------------------------
class AdminDepartmentViewSet(AuditedModelViewSet):
    serializer_class = AdminDepartmentSerializer
    filterset_fields = ['is_active', 'parent']
    search_fields = ['name', 'code']
    ordering_fields = ['name', 'code']

    def get_queryset(self):
        return Department.objects.select_related('head', 'parent').all()


# ---------------------------------------------------------------------------
# 5. Leave types
# ---------------------------------------------------------------------------
class AdminLeaveTypeViewSet(AuditedModelViewSet):
    serializer_class = AdminLeaveTypeSerializer
    filterset_fields = ['is_active', 'is_paid']
    search_fields = ['code', 'name']
    ordering_fields = ['name', 'code']

    def get_queryset(self):
        return LeaveType.objects.all()


# ---------------------------------------------------------------------------
# 6. Bulk leave action
# ---------------------------------------------------------------------------
class AdminBulkLeaveActionView(APIView):
    permission_classes = [IsAdminOrSuperuser]

    def post(self, request):
        serializer = BulkLeaveActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        results = services.bulk_leave_action(
            data['leave_ids'], data['action'], actor=request.user,
            comment=data.get('comment', ''), request=request,
        )
        succeeded = sum(1 for r in results if r['ok'])
        return Response({
            'action': data['action'],
            'total': len(results),
            'succeeded': succeeded,
            'failed': len(results) - succeeded,
            'results': results,
        })


# ---------------------------------------------------------------------------
# 7. Reports metadata
# ---------------------------------------------------------------------------
class AdminReportsView(APIView):
    permission_classes = [IsAdminOrSuperuser]

    def get(self, request):
        return Response({'reports': [
            {'key': 'weekly_attendance', 'name': 'Weekly Attendance', 'granularity': 'week',
             'endpoint': '/api/v1/weekly-summaries/'},
            {'key': 'monthly_attendance', 'name': 'Monthly Attendance', 'granularity': 'month',
             'endpoint': '/api/v1/monthly-summaries/'},
            {'key': 'team_attendance', 'name': 'Team Attendance', 'granularity': 'month',
             'endpoint': '/api/v1/leaves/team-attendance/'},
            {'key': 'balances', 'name': 'Leave Balances', 'granularity': 'year',
             'endpoint': '/api/v1/leave-balances/'},
        ], 'note': 'Exports (CSV/PDF) arrive in Phase 8.'})
