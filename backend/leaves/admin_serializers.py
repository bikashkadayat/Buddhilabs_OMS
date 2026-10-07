"""Serializers used only by the HR/Admin endpoints (leaves/admin_views.py)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from .models import (
    CalendarEvent, Department, EnterpriseLeaveBalance, Holiday, LeavePolicy,
    LeaveType, MonthlyLeaveSummary,
)

User = get_user_model()


class EmployeeSummarySerializer(serializers.ModelSerializer):
    """Row for the admin employee list: identity plus this-year leave stats."""
    full_name = serializers.SerializerMethodField()
    department = serializers.SerializerMethodField()
    used_days = serializers.SerializerMethodField()
    available_days = serializers.SerializerMethodField()
    attendance_percentage = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'full_name', 'email', 'role', 'department', 'is_active',
            'used_days', 'available_days', 'attendance_percentage',
        ]

    def _year(self):
        return self.context.get('year') or timezone.now().year

    def _stats(self, obj):
        cached = getattr(obj, '_lr_stats', None)
        if cached is not None:
            return cached
        year = self._year()
        balances = list(EnterpriseLeaveBalance.objects.filter(user=obj, year=year))
        used = sum((b.used_days for b in balances), Decimal('0'))
        available = sum((b.available_days for b in balances), Decimal('0'))
        atts = [m.attendance_percentage for m in MonthlyLeaveSummary.objects.filter(user=obj, year=year)]
        attendance = (sum(atts, Decimal('0')) / len(atts)) if atts else Decimal('100')
        stats = {'used': used, 'available': available, 'attendance': attendance}
        obj._lr_stats = stats
        return stats

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_department(self, obj):
        if getattr(obj, 'department_ref', None):
            return obj.department_ref.code
        return obj.department or None

    def get_used_days(self, obj):
        return str(self._stats(obj)['used'])

    def get_available_days(self, obj):
        return str(self._stats(obj)['available'])

    def get_attendance_percentage(self, obj):
        return str(round(self._stats(obj)['attendance'], 2))


class BalanceAdjustmentSerializer(serializers.Serializer):
    """Payload for POST .../employees/{id}/adjust-balance/."""
    leave_type = serializers.CharField(help_text="LeaveType code, e.g. ANNUAL")
    year = serializers.IntegerField()
    delta = serializers.DecimalField(max_digits=6, decimal_places=2)
    reason = serializers.CharField(min_length=5)

    def validate_leave_type(self, value):
        if not LeaveType.objects.filter(code__iexact=value).exists():
            raise serializers.ValidationError(f"Unknown leave type code '{value}'.")
        return value.upper()


class AdminLeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = [
            'id', 'code', 'name', 'default_days_per_year', 'is_paid',
            'allow_half_day', 'allow_carry_forward', 'max_carry_forward_days',
            'requires_document', 'min_notice_days', 'is_active', 'display_color',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AdminDepartmentSerializer(serializers.ModelSerializer):
    """
    A department, and the person answerable for it.

    AN ACTIVE DEPARTMENT MUST HAVE A HEAD (Phase DEPARTMENT-GOVERNANCE-HARDENING).
    Departmental task routing, leave approval and every department report end at
    a department head; without one the work has nowhere to go, and the system
    previously dealt with that by quietly routing elsewhere. The rule is enforced
    HERE, at the one writable path a person uses, rather than in
    `Department.save()`: every fixture, seed and data migration in the project
    creates departments before it assigns heads, and a model-level rule would
    make those impossible rather than making the organisation better governed.

    EXISTING HEADLESS DEPARTMENTS ARE NOT DEACTIVATED BY THIS. They keep working
    until somebody sets a head - deactivating a live HR or ICT department would
    take leave, attendance and task scoping down for real staff. They are
    reported instead, loudly: System Health, the dashboard banner, this page and
    the Department Ownership report all name them.
    """
    head_name = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    # Whether the recorded head can still act. A head who has left and been
    # deactivated leaves the department as unowned as one that never had a head.
    head_is_active = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = ['id', 'name', 'code', 'head', 'head_name', 'head_is_active',
                  'parent', 'is_active', 'member_count']

    def get_head_name(self, obj):
        return obj.head.get_full_name() if obj.head else None

    def get_head_is_active(self, obj):
        return bool(obj.head and obj.head.is_active)

    def get_member_count(self, obj):
        return obj.members.count()

    def validate(self, attrs):
        """
        Refuse an active department with nobody at its head, and say what that
        would break rather than just saying no.
        """
        attrs = super().validate(attrs)
        instance = self.instance
        is_active = attrs.get("is_active",
                              getattr(instance, "is_active", True))
        head = attrs.get("head", getattr(instance, "head", None))
        if not is_active:
            return attrs
        if head is None:
            raise serializers.ValidationError({"head": (
                "An active department must have a Department Head. Leave approval "
                "routes to them and every department report is owned by them. "
                "Assign a head, or set the department to inactive.")})
        if not head.is_active:
            raise serializers.ValidationError({"head": (
                f"{head.get_full_name() or head.email} is not an active account, "
                "so they cannot act as Department Head. Choose somebody else.")})
        # THE HEAD MUST HOLD THE DEPARTMENT HEAD ROLE
        # (Phase TASK-PLANNING-AND-DEPARTMENT-OWNERSHIP).
        #
        # Not decoration: every routing decision in the product asks the ROLE,
        # not this column. tasks.permissions.is_department_head and the leave
        # approval chain both test `role == CHECKER`, so an Employee or an HR
        # account recorded here would be a head who could not review their own
        # department's work - the column would say the department was owned
        # while nothing routed to them. The refusal names their actual role so
        # the fix is obvious: change the role, or choose somebody else.
        if head.role != User.Roles.CHECKER:
            raise serializers.ValidationError({"head": (
                f"{head.get_full_name() or head.email} has the "
                f"{head.get_role_display()} role. A Department Head must hold the "
                "Department Head role — task review and leave approval route by "
                "role, so anybody else would be recorded as head without being "
                "able to act as one.")})
        return attrs


class AdminLeavePolicySerializer(serializers.ModelSerializer):
    leave_type_code = serializers.CharField(source='leave_type.code', read_only=True)
    department_code = serializers.CharField(source='department.code', read_only=True, default=None)
    is_effective_now = serializers.SerializerMethodField()

    class Meta:
        model = LeavePolicy
        fields = [
            'id', 'leave_type', 'leave_type_code', 'department', 'department_code',
            'role', 'days_per_year', 'effective_from', 'effective_until',
            'created_by', 'created_at', 'is_effective_now',
        ]
        read_only_fields = ['id', 'created_by', 'created_at']

    def validate_role(self, value):
        # Treat empty string as "all roles" (NULL) so scope comparisons and the
        # unique constraint behave consistently.
        return value or None

    def get_is_effective_now(self, obj):
        from django.utils import timezone
        today = timezone.now().date()
        if obj.effective_from > today:
            return False
        return obj.effective_until is None or obj.effective_until >= today


class AdminHolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Holiday
        fields = ['id', 'date', 'name', 'holiday_type', 'description', 'is_active']


class AdminCalendarEventSerializer(serializers.ModelSerializer):
    """
    Writable serializer for the Nepali-calendar events an admin manages.

    Distinct from the read-only CalendarEventSerializer the calendar grid uses:
    that one locks every field. This one is the admin write path — festivals,
    jayantis, observances and their tithi, several allowed per day.

    `is_public_holiday` here only COLOURS the day on the patro; it does NOT make
    the day non-working. The authority for a day off is the Holiday table
    (managed separately), so marking an event a public holiday and actually
    giving the office the day off are two deliberate actions, not one.
    """
    display_name = serializers.CharField(read_only=True)
    event_type_display = serializers.CharField(
        source="get_event_type_display", read_only=True)

    class Meta:
        model = CalendarEvent
        fields = [
            "id", "date", "name", "name_np", "display_name", "event_type",
            "event_type_display", "tithi", "detail", "is_public_holiday",
            "is_active",
        ]
        read_only_fields = ["id", "display_name", "event_type_display"]
        # Drop the auto UniqueTogetherValidator so `validate()` below owns the
        # duplicate case and returns a friendlier, name-keyed message than the
        # default "the fields date, name must make a unique set".
        validators = []

    def validate(self, attrs):
        # The model's UniqueConstraint(date, name) raises a 500-looking
        # IntegrityError on a clash; surface it as a clean field error instead.
        date = attrs.get("date", getattr(self.instance, "date", None))
        name = attrs.get("name", getattr(self.instance, "name", None))
        qs = CalendarEvent.objects.filter(date=date, name=name)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {"name": "An event with this name already exists on this date."})
        return attrs


class BulkLeaveActionSerializer(serializers.Serializer):
    """Payload for POST .../leaves/bulk-action/."""
    leave_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    action = serializers.ChoiceField(choices=['approve', 'reject'])
    comment = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        if attrs['action'] == 'reject' and len(attrs.get('comment', '').strip()) < 5:
            raise serializers.ValidationError(
                {'comment': 'A reason (>= 5 chars) is required when rejecting.'}
            )
        return attrs
