from django.contrib.auth import get_user_model
from rest_framework import serializers
from .models import Leave, LeaveBalance

User = get_user_model()

class LeaveBalanceSerializer(serializers.ModelSerializer):
    remaining = serializers.ReadOnlyField()

    class Meta:
        model = LeaveBalance
        fields = ['id', 'leave_type', 'year', 'total_allocated', 'used_so_far', 'remaining']


class LeaveSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    approver_name = serializers.CharField(source='approver.get_full_name', read_only=True)
    # Phase 2.6: two-stage review trail (read-only; set by workflow endpoints).
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    department_head_name = serializers.CharField(source='department_head_reviewer.get_full_name', read_only=True)
    board_reviewer_name = serializers.CharField(source='board_reviewer.get_full_name', read_only=True, default=None)
    hr_name = serializers.CharField(source='hr_reviewer.get_full_name', read_only=True)
    department_name = serializers.CharField(source='user.department_name', read_only=True)
    # Detailed-review payload: everything an approver needs to decide, so no one
    # approves blind. All read-only / computed - no new DB columns.
    employee_id = serializers.CharField(source='user.employee_id', read_only=True)
    total_days = serializers.SerializerMethodField()
    remaining_balance = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()
    start_date_bs = serializers.SerializerMethodField()
    end_date_bs = serializers.SerializerMethodField()
    created_at_bs = serializers.SerializerMethodField()

    class Meta:
        model = Leave
        fields = [
            'id', 'user', 'user_name', 'employee_id', 'department_name', 'leave_type',
            'start_date', 'start_date_bs', 'end_date', 'end_date_bs', 'day_portion', 'total_days',
            'reason', 'handover_notes', 'status', 'status_display', 'approver', 'approver_name',
            'department_head_reviewer', 'department_head_name', 'department_head_action_date',
            'board_reviewer', 'board_reviewer_name', 'board_action_date',
            'hr_reviewer', 'hr_name', 'hr_action_date', 'remarks',
            'remaining_balance', 'documents', 'timeline', 'created_at', 'created_at_bs',
        ]
        read_only_fields = [
            'status', 'user', 'department_head_reviewer', 'department_head_action_date', 'board_reviewer', 'board_action_date',
            'hr_reviewer', 'hr_action_date', 'remarks',
        ]

    def get_total_days(self, obj):
        try:
            days = (obj.end_date - obj.start_date).days + 1
            if obj.day_portion in (Leave.DayPortion.FIRST_HALF, Leave.DayPortion.SECOND_HALF):
                return days * 0.5
            return days
        except Exception:
            return None

    def get_remaining_balance(self, obj):
        bal = LeaveBalance.objects.filter(
            user=obj.user, leave_type=obj.leave_type, year=obj.start_date.year
        ).first()
        if not bal:
            return None
        return {
            'year': bal.year,
            'total_allocated': bal.total_allocated,
            'used_so_far': bal.used_so_far,
            'remaining': max(0, bal.total_allocated - bal.used_so_far),
        }

    def get_documents(self, obj):
        # Leaves currently carry no file attachments; returned for a stable UI
        # contract (the panel shows "None"). Wire real uploads here when added.
        return []

    def _bs(self, value):
        from config.nepali_dates import to_bs
        return to_bs(value)

    def get_start_date_bs(self, obj):
        return self._bs(obj.start_date)

    def get_end_date_bs(self, obj):
        return self._bs(obj.end_date)

    def get_created_at_bs(self, obj):
        return self._bs(obj.created_at)

    def get_timeline(self, obj):
        """Ordered approval timeline: Employee -> Department Head -> HR."""
        from config.nepali_dates import to_bs

        def stage(label, actor, when, state, remarks=''):
            name = (actor.get_full_name() or actor.username) if actor else None
            return {
                'stage': label,
                'name': name,
                'date_ad': when.isoformat() if when else None,
                'date_bs': to_bs(when) if when else None,
                'status': state,
                'remarks': remarks or '',
            }

        applicant = obj.user
        steps = [stage('Employee', applicant, obj.created_at, 'submitted')]

        # A Department Head's or Board member's leave has one decider: the Board
        # (Phase BOD). Drawing the usual Department Head / HR stages over it would
        # show two people who were never asked.
        from .approvals import routes_to_board
        if routes_to_board(applicant) or obj.board_reviewer_id:
            state = {Leave.Status.APPROVED: 'approved',
                     Leave.Status.REJECTED: 'rejected'}.get(obj.status, 'pending')
            decider = obj.board_reviewer or (obj.approver if obj.status != Leave.Status.PENDING else None)
            steps.append(stage('Board of Directors', decider,
                               obj.board_action_date or (obj.hr_action_date if decider else None),
                               state, obj.remarks if state != 'pending' else ''))
            return steps

        if obj.department_head_reviewer_id or obj.status in (
            Leave.Status.PENDING_HR, Leave.Status.APPROVED, Leave.Status.REJECTED
        ):
            dh_state = 'approved' if (obj.department_head_action_date or obj.status in (
                Leave.Status.PENDING_HR, Leave.Status.APPROVED)) else 'pending'
            if obj.status == Leave.Status.REJECTED and not obj.hr_action_date:
                dh_state = 'rejected'
            steps.append(stage('Department Head', obj.department_head_reviewer,
                               obj.department_head_action_date, dh_state, obj.remarks))
        else:
            steps.append(stage('Department Head', None, None, 'pending'))

        hr_state = 'pending'
        if obj.status == Leave.Status.APPROVED:
            hr_state = 'approved'
        elif obj.status == Leave.Status.REJECTED and obj.hr_action_date:
            hr_state = 'rejected'
        steps.append(stage('HR', obj.hr_reviewer, obj.hr_action_date, hr_state,
                          obj.remarks if obj.hr_action_date else ''))
        return steps

    def validate(self, data):
        if data['start_date'] > data['end_date']:
            raise serializers.ValidationError("End date must be after start date.")
        # A half day is only meaningful on a single date — reject a half-day
        # request that spans a range so the balance can't silently halve a week.
        portion = data.get('day_portion', Leave.DayPortion.FULL)
        if portion in (Leave.DayPortion.FIRST_HALF, Leave.DayPortion.SECOND_HALF) \
                and data['start_date'] != data['end_date']:
            raise serializers.ValidationError(
                "A half-day leave must start and end on the same date.")
        if 'approver' in data and data['approver']:
            try:
                approver = User.objects.get(id=data['approver'].id if hasattr(data['approver'], 'id') else data['approver'])
                if approver.role not in [User.Roles.CHECKER, User.Roles.APPROVER,
                                         User.Roles.BOD, User.Roles.ADMIN]:
                    raise serializers.ValidationError("Reporting manager must be a Department Head, HR, or Admin.")
            except User.DoesNotExist:
                raise serializers.ValidationError("Selected approver does not exist.")
        return data


# ===========================================================================
# Phase 4 - Enterprise Leave Records serializers (appended)
# ===========================================================================
from .models import (  # noqa: E402
    Department,
    EnterpriseLeaveBalance,
    Holiday,
    LeaveDayRecord,
    LeavePolicy,
    LeaveType,
    MonthlyLeaveSummary,
    WeeklyLeaveSummary,
)


class UserMiniSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "full_name", "email", "role", "department"]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = [
            "id", "code", "name", "default_days_per_year", "is_paid",
            "allow_half_day", "allow_carry_forward", "max_carry_forward_days",
            "requires_document", "min_notice_days", "is_active", "display_color",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class DepartmentSerializer(serializers.ModelSerializer):
    head = UserMiniSerializer(read_only=True)

    class Meta:
        model = Department
        fields = ["id", "name", "code", "head", "parent", "is_active"]


class LeavePolicySerializer(serializers.ModelSerializer):
    leave_type_code = serializers.CharField(source="leave_type.code", read_only=True)
    department_code = serializers.CharField(source="department.code", read_only=True, default=None)

    class Meta:
        model = LeavePolicy
        fields = [
            "id", "leave_type", "leave_type_code", "department", "department_code",
            "role", "days_per_year", "effective_from", "effective_until",
            "created_by", "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_at"]


class CalendarEventSerializer(serializers.ModelSerializer):
    """One dated entry for the Nepali calendar."""
    display_name = serializers.CharField(read_only=True)
    event_type_display = serializers.CharField(
        source="get_event_type_display", read_only=True)

    class Meta:
        from .models import CalendarEvent
        model = CalendarEvent
        fields = ["id", "date", "name", "name_np", "display_name", "tithi",
                  "event_type", "event_type_display", "detail", "is_public_holiday"]
        read_only_fields = fields


class HolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = ["id", "date", "name", "holiday_type", "description", "is_active"]


class LeaveDayRecordSerializer(serializers.ModelSerializer):
    """Compact per-day record for calendar rendering (color-coded by type)."""
    leave_type_code = serializers.CharField(source="leave_type.code", read_only=True)
    display_color = serializers.CharField(source="leave_type.display_color", read_only=True)
    portion_days = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)

    class Meta:
        model = LeaveDayRecord
        fields = [
            "id", "date", "day_portion", "portion_days", "leave_type",
            "leave_type_code", "display_color", "status",
            "is_holiday", "is_weekend", "week_number", "month", "year",
        ]


class EnterpriseLeaveBalanceSerializer(serializers.ModelSerializer):
    """Phase 4 balance with the computed available_days. (Named to avoid
    clashing with the Level 1 LeaveBalanceSerializer above.)"""
    leave_type_code = serializers.CharField(source="leave_type.code", read_only=True)
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)
    available_days = serializers.DecimalField(max_digits=7, decimal_places=2, read_only=True)

    class Meta:
        model = EnterpriseLeaveBalance
        fields = [
            "id", "user", "leave_type", "leave_type_code", "leave_type_name", "year",
            "entitled_days", "carried_forward_days", "used_days", "pending_days",
            "encashed_days", "forfeited_days", "adjustment_days", "available_days",
            "last_recomputed_at",
        ]
        read_only_fields = fields


class WeeklyLeaveSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = WeeklyLeaveSummary
        fields = [
            "id", "user", "year", "week_number", "week_start_date", "week_end_date",
            "total_leave_days", "by_type", "approved_days", "pending_days",
            "rejected_days", "working_days", "attendance_percentage", "last_recomputed_at",
        ]
        read_only_fields = fields


class MonthlyLeaveSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyLeaveSummary
        fields = [
            "id", "user", "year", "month", "total_leave_days", "by_type",
            "approved_days", "pending_days", "working_days", "attendance_percentage",
            "carry_forward_earned", "last_recomputed_at",
        ]
        read_only_fields = fields


class LeaveHistorySerializer(serializers.Serializer):
    """
    Rich, read-only composite for a user's year: profile, all balances, recent
    leaves, and the monthly summaries. Built by the my-history endpoint.
    """
    user = UserMiniSerializer(read_only=True)
    year = serializers.IntegerField(read_only=True)
    balances = EnterpriseLeaveBalanceSerializer(many=True, read_only=True)
    recent_leaves = LeaveSerializer(many=True, read_only=True)
    monthly_summaries = MonthlyLeaveSummarySerializer(many=True, read_only=True)
