"""Serializers for policy administration and the WFH workflow.

Every serializer runs the model's ``clean()`` through ``validate()`` so the
invariants live in one place: an overnight shift, a half-day threshold above the
full-day threshold, or a user-scoped assignment with no user are rejected
identically whether they arrive via the API, the Django admin or a shell.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import AttendancePolicy, EmployeeShift, PolicyAssignment, Shift, WFHRequest


class _ModelCleanMixin:
    """Run the model's own ``clean()`` as part of DRF validation."""

    def validate(self, attrs):
        instance = self.instance
        if instance is None:
            instance = self.Meta.model(**attrs)
        else:
            for field, value in attrs.items():
                setattr(instance, field, value)
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
        return attrs


class ShiftSerializer(_ModelCleanMixin, serializers.ModelSerializer):
    class Meta:
        model = Shift
        fields = ["id", "code", "name", "start_time", "end_time", "grace_minutes",
                  "break_minutes", "crosses_midnight", "is_active",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_crosses_midnight(self, value):
        if value:
            raise serializers.ValidationError("Overnight shifts are not supported yet.")
        return value


class AttendancePolicySerializer(_ModelCleanMixin, serializers.ModelSerializer):
    default_shift_name = serializers.CharField(source="default_shift.name", read_only=True)
    assignment_count = serializers.IntegerField(source="assignments.count", read_only=True)

    class Meta:
        model = AttendancePolicy
        fields = [
            "id", "name", "description", "is_active",
            "office_start_time", "grace_minutes", "absent_cutoff_time",
            "late_after_time", "half_day_after_time",
            "half_day_hours", "full_day_hours", "deduct_breaks",
            "overtime_threshold_hours", "overtime_min_minutes",
            "comp_off_enabled", "comp_off_on_saturday", "comp_off_on_holiday",
            "comp_off_min_hours", "comp_off_half_day_hours", "comp_off_full_day_hours",
            "default_shift", "default_shift_name", "assignment_count",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at",
                            "default_shift_name", "assignment_count"]


class PolicyAssignmentSerializer(_ModelCleanMixin, serializers.ModelSerializer):
    policy_name = serializers.CharField(source="policy.name", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)

    class Meta:
        model = PolicyAssignment
        fields = ["id", "policy", "policy_name", "scope", "department", "department_name",
                  "user", "user_name", "effective_from", "effective_until", "created_at"]
        read_only_fields = ["id", "created_at", "policy_name", "department_name", "user_name"]


class EmployeeShiftSerializer(_ModelCleanMixin, serializers.ModelSerializer):
    shift_name = serializers.CharField(source="shift.name", read_only=True)
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)

    class Meta:
        model = EmployeeShift
        fields = ["id", "user", "user_name", "shift", "shift_name",
                  "effective_from", "effective_until", "note", "created_at"]
        read_only_fields = ["id", "created_at", "shift_name", "user_name"]


class WFHRequestSerializer(_ModelCleanMixin, serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)
    reviewed_by_name = serializers.CharField(source="reviewed_by.get_full_name", read_only=True)

    class Meta:
        model = WFHRequest
        fields = ["id", "user", "user_name", "start_date", "end_date", "reason",
                  "status", "reviewed_by", "reviewed_by_name", "reviewed_at",
                  "review_note", "created_at", "updated_at"]
        # An employee submits dates and a reason; everything else is the
        # reviewer's or the system's to set.
        read_only_fields = ["id", "user", "user_name", "status", "reviewed_by",
                            "reviewed_by_name", "reviewed_at", "review_note",
                            "created_at", "updated_at"]


class WFHReviewSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)
