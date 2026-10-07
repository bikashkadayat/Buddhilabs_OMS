"""
Circular read and write shapes.

Capability flags on the detail payload are the same contract the memo and minute
modules use: the UI renders its action bar purely from the server's answer, so
every authorization rule has exactly one home and the client never re-derives who
may do what.
"""
from rest_framework import serializers

from .models import (
    Circular, CircularAttachment, CircularBroadcast, CircularWorkflowStep,
)
from .sanitizers import sanitize_circular_html


class UserMiniSerializer(serializers.Serializer):
    """The three fields any name-with-context display needs."""
    id = serializers.UUIDField(read_only=True)
    full_name = serializers.SerializerMethodField()
    designation = serializers.CharField(read_only=True, allow_null=True)
    department = serializers.SerializerMethodField()

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_department(self, obj):
        return getattr(obj, "department_name", None) or getattr(obj, "department", "")


class CircularWorkflowStepSerializer(serializers.ModelSerializer):
    assignee = UserMiniSerializer(read_only=True)
    name = serializers.CharField(source="display_name", read_only=True)
    role_label = serializers.CharField(source="get_role_type_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = CircularWorkflowStep
        fields = ["id", "sequence", "assignee", "name", "role_type", "role_label",
                  "status", "status_label", "designation", "department_label",
                  "activated_at", "acted_at", "remarks"]
        read_only_fields = fields


class CircularAttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    uploaded_by = UserMiniSerializer(read_only=True)
    size_label = serializers.SerializerMethodField()

    class Meta:
        model = CircularAttachment
        fields = ["id", "original_name", "extension", "size", "size_label",
                  "uploaded_by", "uploaded_at", "url"]
        read_only_fields = fields

    def get_url(self, obj):
        """Short-lived and bound to the requesting user - never a raw media path."""
        from documents.protected_media import signed_media_url
        viewer = getattr(self.context.get("request"), "user", None)
        return signed_media_url(obj.file.name, ttl=300, download=True, user=viewer)

    def get_size_label(self, obj):
        size = obj.size or 0
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size} B"


class CircularBroadcastSerializer(serializers.ModelSerializer):
    audience_kind = serializers.CharField(source="get_audience_display",
                                          read_only=True)

    class Meta:
        model = CircularBroadcast
        fields = ["id", "sequence", "audience", "audience_kind", "audience_label",
                  "recipient_count", "already_present_count", "broadcast_by_name",
                  "broadcast_at", "remarks", "include_child_departments"]
        read_only_fields = fields


class CircularListSerializer(serializers.ModelSerializer):
    """
    The list row. Deliberately narrow: no registers, no timeline, no per-recipient
    anything - a fifty-row page must not carry fifty distribution lists.
    """
    created_by = UserMiniSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    category_label = serializers.CharField(source="get_category_display",
                                           read_only=True)
    classification_label = serializers.CharField(
        source="get_classification_display", read_only=True)
    priority_label = serializers.CharField(source="get_priority_display",
                                           read_only=True)
    department_label = serializers.SerializerMethodField()
    pending_with = serializers.SerializerMethodField()
    reach = serializers.SerializerMethodField()

    class Meta:
        model = Circular
        fields = ["id", "circular_number", "subject", "category", "category_label",
                  "classification", "classification_label", "priority",
                  "priority_label", "status", "status_label", "issue_date",
                  "created_by", "department_label", "created_at", "issued_at",
                  "issued_by_name", "broadcast_at", "archived_at",
                  "acknowledgement_required", "pending_with", "reach",
                  "external_reference"]
        read_only_fields = fields

    def get_department_label(self, obj):
        return obj.resolved_department_name()

    def get_pending_with(self, obj):
        from .workflow import pending_with
        return pending_with(obj)

    def get_reach(self, obj):
        """
        Recipient/read counts, from ANNOTATIONS the viewset adds - never from a
        per-row query. Falls back to None rather than counting, so a caller that
        forgot the annotation gets a visibly missing figure instead of a list that
        silently costs one query per row.
        """
        total = getattr(obj, "recipient_total", None)
        if total is None:
            return None
        read = getattr(obj, "read_total", 0) or 0
        return {"recipients": total, "read": read, "unread": total - read,
                "percent": round(read * 100 / total) if total else 0}


class CircularDetailSerializer(CircularListSerializer):
    """Full payload: content, chain, registers and per-user capabilities."""
    content = serializers.CharField(read_only=True)
    workflow_steps = CircularWorkflowStepSerializer(many=True, read_only=True)
    attachments = CircularAttachmentSerializer(many=True, read_only=True)
    broadcasts = serializers.SerializerMethodField()
    tracker = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()
    read_summary = serializers.SerializerMethodField()
    acknowledgement = serializers.SerializerMethodField()
    my_acknowledgement = serializers.SerializerMethodField()
    my_step = serializers.SerializerMethodField()
    is_recipient = serializers.SerializerMethodField()
    is_read_only = serializers.BooleanField(read_only=True)
    memo_reference_label = serializers.SerializerMethodField()
    minute_reference_label = serializers.SerializerMethodField()

    # Capability flags. The UI's action bar is rendered purely from these.
    can_edit = serializers.SerializerMethodField()
    can_edit_chain = serializers.SerializerMethodField()
    can_submit = serializers.SerializerMethodField()
    can_act = serializers.SerializerMethodField()
    can_broadcast = serializers.SerializerMethodField()
    can_archive = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    can_acknowledge = serializers.SerializerMethodField()
    can_view_registers = serializers.SerializerMethodField()
    can_export = serializers.SerializerMethodField()

    class Meta(CircularListSerializer.Meta):
        fields = CircularListSerializer.Meta.fields + [
            "content", "workflow_steps", "attachments", "broadcasts", "tracker",
            "timeline", "read_summary", "acknowledgement", "my_acknowledgement",
            "my_step", "is_recipient", "is_read_only", "updated_at",
            "submitted_at", "acknowledgement_due_days",
            "memo_reference", "memo_reference_label",
            "minute_reference", "minute_reference_label",
            "can_edit", "can_edit_chain", "can_submit", "can_act", "can_broadcast",
            "can_archive", "can_cancel", "can_delete", "can_acknowledge",
            "can_view_registers", "can_export",
        ]
        read_only_fields = fields

    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def get_broadcasts(self, obj):
        from .broadcast import broadcast_history
        return broadcast_history(obj)

    def get_tracker(self, obj):
        from .workflow import build_tracker
        return build_tracker(obj)

    def get_timeline(self, obj):
        from .workflow import build_timeline
        return build_timeline(obj)

    def get_read_summary(self, obj):
        from .broadcast import read_summary
        return read_summary(obj)

    def get_acknowledgement(self, obj):
        from .broadcast import acknowledgement_summary
        return acknowledgement_summary(obj)

    def get_my_acknowledgement(self, obj):
        """The caller's own row - so the panel can show their state without the register."""
        from .models import CircularAcknowledgement
        user = self._user()
        if user is None or not obj.acknowledgement_required:
            return None
        row = CircularAcknowledgement.objects.filter(
            recipient__circular=obj, recipient__user=user).first()
        if row is None:
            return None
        return {"state": row.state, "state_label": row.get_state_display(),
                "remarks": row.remarks, "responded_at": row.responded_at,
                "due_date": row.due_date, "is_late": row.is_late}

    def get_my_step(self, obj):
        user = self._user()
        if user is None:
            return None
        step = next((s for s in obj.workflow_steps.all()
                     if s.assignee_id == user.id), None)
        if step is None:
            return None
        return {
            "id": str(step.id), "sequence": step.sequence,
            "role_type": step.role_type,
            "role_label": step.get_role_type_display(),
            "status": step.status,
            "is_my_turn": step.status == CircularWorkflowStep.StepStatus.ACTIVE,
            # A reviewer must say something; an issuer's signature speaks for itself.
            "requires_remarks": step.role_type == CircularWorkflowStep.RoleType.REVIEWER,
        }

    def get_is_recipient(self, obj):
        user = self._user()
        return bool(user and obj.recipients.filter(user=user).exists())

    def get_memo_reference_label(self, obj):
        return obj.memo_reference.memo_number if obj.memo_reference_id else None

    def get_minute_reference_label(self, obj):
        return obj.minute_reference.minute_number if obj.minute_reference_id else None

    def _flag(self, name, obj):
        from . import permissions as perms
        user = self._user()
        return bool(user and getattr(perms, name)(user, obj))

    def get_can_edit(self, obj):
        return self._flag("can_edit", obj)

    def get_can_edit_chain(self, obj):
        return self._flag("can_edit_chain", obj)

    def get_can_submit(self, obj):
        return self._flag("can_submit", obj)

    def get_can_act(self, obj):
        return self._flag("can_act", obj)

    def get_can_broadcast(self, obj):
        from .broadcast import can_broadcast
        user = self._user()
        return bool(user and can_broadcast(user, obj))

    def get_can_archive(self, obj):
        return self._flag("can_archive", obj)

    def get_can_cancel(self, obj):
        return self._flag("can_cancel", obj)

    def get_can_delete(self, obj):
        return self._flag("can_delete", obj)

    def get_can_acknowledge(self, obj):
        return self._flag("can_acknowledge", obj)

    def get_can_view_registers(self, obj):
        return self._flag("can_view_registers", obj)

    def get_can_export(self, obj):
        return self._flag("can_export", obj)


class CircularWriteSerializer(serializers.ModelSerializer):
    """
    Create/update shape. Content passes through the sanitizer on the way in - that
    is the write gate; the browser sanitizes again on read and the PDF a third time,
    because a stale allowlist must not become a silent difference between what was
    saved and what is shown.
    """
    class Meta:
        model = Circular
        fields = ["subject", "content", "category", "classification", "priority",
                  "issue_date", "department", "external_reference",
                  "memo_reference", "minute_reference",
                  "acknowledgement_required", "acknowledgement_due_days"]

    def validate_content(self, value):
        return sanitize_circular_html(value)

    def validate_acknowledgement_due_days(self, value):
        if value < 1 or value > 90:
            raise serializers.ValidationError(
                "Choose between 1 and 90 days. A deadline outside that range is "
                "either impossible to meet or not a deadline.")
        return value


# ---------------------------------------------------------------------------
# Action shapes
# ---------------------------------------------------------------------------
class ChainRowSerializer(serializers.Serializer):
    assignee_id = serializers.UUIDField()
    role_type = serializers.ChoiceField(
        choices=CircularWorkflowStep.RoleType.choices)


class ChainInputSerializer(serializers.Serializer):
    workflow = ChainRowSerializer(many=True)


class CircularActionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["proceed", "reject"])
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class BroadcastSerializer(serializers.Serializer):
    audience = serializers.ChoiceField(choices=CircularBroadcast.Audience.choices)
    department_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list)
    group_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list)
    employee_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list)
    include_children = serializers.BooleanField(required=False, default=True)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class AcknowledgeSerializer(serializers.Serializer):
    accept = serializers.BooleanField(default=True)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class ImportSerializer(serializers.Serializer):
    """Exactly one source; the service enforces that rather than the shape."""
    memo_id = serializers.UUIDField(required=False, allow_null=True)
    minute_id = serializers.UUIDField(required=False, allow_null=True)


class RemarksSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
