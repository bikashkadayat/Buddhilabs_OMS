"""Serializers for the correction workflow.

Validation lives on the model (``AttendanceCorrectionRequest.clean``) and is run
from here, so the API, the Django admin and a shell script all reject the same
things — a naive timestamp, a requested time that falls on a different day, a
request that asks for nothing.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from config.uploads import validate_attachment

from ..models import Attendance
from .models import AttendanceCorrectionRequest


class AttendanceCorrectionSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.get_full_name", read_only=True)
    employee_code = serializers.CharField(source="employee.employee_id", read_only=True)
    department = serializers.CharField(source="employee.department_ref.name", read_only=True)
    manager_name = serializers.CharField(source="manager.get_full_name", read_only=True)
    hr_actor_name = serializers.CharField(source="hr_actor.get_full_name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    has_attachment = serializers.SerializerMethodField()

    # An explicit status is optional, and restricted to what HR could set by
    # hand — WORK_FROM_HOME stays derived from an approved request plus a real
    # check-in and must not be reachable by typing it into a correction.
    requested_status = serializers.ChoiceField(
        choices=Attendance.MANUAL_STATUSES, required=False, allow_blank=True, default="")

    class Meta:
        model = AttendanceCorrectionRequest
        fields = [
            "id", "employee", "employee_name", "employee_code", "department",
            "attendance_date",
            "requested_check_in", "requested_check_out", "requested_status",
            "reason", "attachment", "has_attachment", "remarks",
            "previous_check_in", "previous_check_out", "previous_status",
            "previous_source", "had_attendance_row",
            "status", "status_display",
            "manager", "manager_name", "manager_action_at", "manager_remarks",
            "hr_actor", "hr_actor_name", "hr_action_at", "hr_remarks",
            "rejected_by", "rejection_reason",
            "applied_at", "reverted_at", "attendance",
            "created_at", "updated_at",
        ]
        # An employee supplies the date, the requested values, a reason and an
        # attachment. Everything else belongs to the workflow.
        read_only_fields = [
            "id", "employee", "employee_name", "employee_code", "department",
            "has_attachment",
            "previous_check_in", "previous_check_out", "previous_status",
            "previous_source", "had_attendance_row",
            "status", "status_display",
            "manager", "manager_name", "manager_action_at", "manager_remarks",
            "hr_actor", "hr_actor_name", "hr_action_at", "hr_remarks",
            "rejected_by", "rejection_reason",
            "applied_at", "reverted_at", "attendance",
            "created_at", "updated_at",
        ]

    def get_has_attachment(self, obj):
        return bool(obj.attachment)

    def validate_attachment(self, value):
        """Phase 11 (audit finding M2).

        This field shipped in Phase 9 with no size limit, no extension
        allowlist and no content check — an employee could attach a 500MB file,
        or an HTML document named ``evidence.pdf`` that a department head would
        then open. Memo attachments had all three checks from the start; the
        logic simply was not reachable from here. It now is.
        """
        return validate_attachment(value)

    def validate(self, attrs):
        instance = self.instance or AttendanceCorrectionRequest()
        for field, value in attrs.items():
            setattr(instance, field, value)
        if instance.employee_id is None:
            instance.employee = self.context["request"].user
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
        return attrs


class CorrectionActionSerializer(serializers.Serializer):
    """Remarks accompanying an approve; reason accompanying a reject/revert."""
    remarks = serializers.CharField(required=False, allow_blank=True, default="",
                                    max_length=255)
    reason = serializers.CharField(required=False, allow_blank=True, default="",
                                   max_length=255)
