"""
Minute serializers.

THE LIST SERIALIZER IS SLIM ON PURPOSE
--------------------------------------
`MinuteListSerializer` omits the agenda body. A minute's body is authored HTML that
can run to hundreds of kilobytes; returning it on a 50-row list page would dominate
the response for data no list renders. Everything the list DOES compute per row
(pending-with, the acknowledgement tally) is derived in PYTHON from prefetched rows,
never with `.filter()` on a related manager - that bypasses prefetch_related and fires
a query per row.

CAPABILITY FLAGS, NOT CLIENT-SIDE RULES
---------------------------------------
The detail serializer emits `can_*` booleans computed from minutes.permissions. The UI
renders its buttons purely from those, so authorization has exactly one home on the
server and the client cannot offer an action the API would refuse.
"""
from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    Minute, MinuteAttachment, MinuteAuditLog, MinuteInvolvement, MinuteParticipant,
    MinuteType,
)
from .services import sanitize_minute_html, user_snapshot
from . import permissions as perms
from . import workflow

User = get_user_model()


class UserMiniSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "full_name", "email", "designation", "department"]
        read_only_fields = fields

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------
class MinuteTypeSerializer(serializers.ModelSerializer):
    """DEPARTMENT / BRANCH / MANCOM / OTHERS - the manual's Minute Type dropdown."""

    class Meta:
        model = MinuteType
        fields = ["id", "code", "name", "ordering"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Children
# ---------------------------------------------------------------------------
class MinuteParticipantSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)
    name = serializers.CharField(source="display_name", read_only=True)
    attendance_label = serializers.CharField(
        source="get_attendance_display", read_only=True)
    ack_status_label = serializers.CharField(
        source="get_ack_status_display", read_only=True)
    must_acknowledge = serializers.BooleanField(read_only=True)
    ack_due_date = serializers.DateField(read_only=True)
    is_late = serializers.BooleanField(read_only=True)

    class Meta:
        model = MinuteParticipant
        fields = ["id", "user", "name", "designation", "department_label",
                  "attendance", "attendance_label",
                  "ack_status", "ack_status_label", "must_acknowledge",
                  "acknowledged_at", "remarks", "ack_due_date", "is_late",
                  "reminded_at"]
        read_only_fields = fields


class ParticipantRowWriteSerializer(serializers.Serializer):
    """One row of the three pickers on the form: present, absent or invitee."""
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True), source="user")
    attendance = serializers.ChoiceField(
        choices=MinuteParticipant.Attendance.choices,
        default=MinuteParticipant.Attendance.PRESENT)


class ParticipantListWriteSerializer(serializers.Serializer):
    participants = ParticipantRowWriteSerializer(many=True)


class MinuteInvolvementSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)
    assignment_type_label = serializers.CharField(
        source="get_assignment_type_display", read_only=True)

    class Meta:
        model = MinuteInvolvement
        fields = ["id", "user", "assignment_type", "assignment_type_label", "note",
                  "created_at", "acted_at"]
        read_only_fields = fields


class InvolvementRowWriteSerializer(serializers.Serializer):
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True), source="user")
    assignment_type = serializers.ChoiceField(
        choices=MinuteInvolvement.AssignmentType.choices,
        default=MinuteInvolvement.AssignmentType.INFORMATION_ONLY)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class InvolvementListWriteSerializer(serializers.Serializer):
    involvements = InvolvementRowWriteSerializer(many=True)


class MinuteAttachmentSerializer(serializers.ModelSerializer):
    uploaded_by = UserMiniSerializer(read_only=True)
    size_label = serializers.CharField(read_only=True)
    is_previewable = serializers.BooleanField(read_only=True)
    download_url = serializers.SerializerMethodField()
    versions = serializers.SerializerMethodField()

    class Meta:
        model = MinuteAttachment
        fields = ["id", "original_name", "content_type", "size", "size_label",
                  "version", "is_current", "is_previewable", "download_url",
                  "uploaded_by", "uploaded_at", "versions"]
        read_only_fields = fields

    def get_download_url(self, obj):
        """
        A signed, expiring URL. There is no public /media/ route in this project, so
        a raw FileField.url would 404.
        """
        from documents.protected_media import signed_media_url
        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        if not obj.file or viewer is None:
            return None
        # Bound to the viewer and short-lived, the same way the memo module issues
        # them - the link is useless if it leaks out of this response.
        return signed_media_url(obj.file.name, ttl=300, download=True, user=viewer)

    def get_versions(self, obj):
        """Superseded revisions, newest first, so the panel can offer history."""
        older = []
        row = obj.replaces
        while row is not None:
            older.append({"id": str(row.id), "version": row.version,
                          "original_name": row.original_name,
                          "uploaded_at": row.uploaded_at})
            row = row.replaces
        return older


class MinuteAuditLogSerializer(serializers.ModelSerializer):
    actor_display = serializers.CharField(read_only=True)
    action_label = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = MinuteAuditLog
        fields = ["id", "action", "action_label", "actor_display", "remarks",
                  "ip_address", "metadata", "created_at"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
class MinuteWriteSerializer(serializers.ModelSerializer):
    """
    Create and edit. Exactly the manual's form fields (p.4) plus the optional subject.

    `agenda_body` is sanitized on the way in rather than on the way out: the stored
    value is the safe one, so every reader - the API, the PDF, the Excel export -
    gets the same sanitized HTML without each having to remember to sanitize.
    """
    fro_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True), source="fro",
        required=False, allow_null=True)

    class Meta:
        model = Minute
        fields = ["subject", "reference_number", "minute_type", "meeting_date",
                  "meeting_time", "fro_id", "agenda_body",
                  "acknowledgement_due_days"]

    def validate_agenda_body(self, value):
        return sanitize_minute_html(value or "")

    def validate(self, attrs):
        # Defence in depth on edit: defer to the SAME authority the view uses,
        # `perms.can_edit`, rather than a second copy of the status rule. Since
        # the Sept 2026 policy lets a minute's author edit it at any stage
        # (archived included) while everyone else is still bound by status, the
        # old `is_editable` check here would wrongly 400 the author's edit of a
        # finalized minute — which is exactly what it started doing.
        instance = self.instance
        if instance is not None:
            request = self.context.get("request")
            user = getattr(request, "user", None)
            if not (user and perms.can_edit(user, instance)):
                raise serializers.ValidationError(
                    {"detail": f"A minute that is {instance.get_status_display().lower()} "
                               "can no longer be edited."})
        return attrs

    def save(self, **kwargs):
        minute = super().save(**kwargs)
        # Keep the FRO name snapshot in step with the foreign key.
        snapshot = user_snapshot(minute.fro) if minute.fro_id else {"name": ""}
        if minute.fro_name != snapshot["name"]:
            minute.fro_name = snapshot["name"]
            minute.save(update_fields=["fro_name"])
        return minute


class AcknowledgementSerializer(serializers.Serializer):
    """
    Acknowledging carries an optional remark and nothing else.

    The manual's OTP field (p.9) is deliberately absent - see minutes/workflow.py.
    """
    remarks = serializers.CharField(required=False, allow_blank=True)


class ReviewReturnSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
class MinuteListSerializer(serializers.ModelSerializer):
    """
    One row of any of the minute lists.

    The columns the manual's lists actually show: Minute Number, Reference No., Date,
    Type, Initiated By (p.10), plus the status and acknowledgement tally the queues
    need.
    """
    type_label = serializers.CharField(source="minute_type.name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    created_by = UserMiniSerializer(read_only=True)
    title = serializers.CharField(source="display_title", read_only=True)
    acknowledgement = serializers.SerializerMethodField()
    pending_with = serializers.SerializerMethodField()

    class Meta:
        model = Minute
        fields = ["id", "minute_number", "reference_number", "subject", "title",
                  "minute_type", "type_label", "meeting_date", "meeting_time",
                  "status", "status_label", "created_by", "department_name",
                  "acknowledgement", "pending_with",
                  "created_at", "archived_at"]
        read_only_fields = fields

    def get_acknowledgement(self, obj):
        return workflow.acknowledgement_summary(obj)

    def get_pending_with(self, obj):
        return workflow.pending_with(obj)


class MinuteDetailSerializer(MinuteListSerializer):
    fro = UserMiniSerializer(read_only=True)
    participants = MinuteParticipantSerializer(many=True, read_only=True)
    involvements = MinuteInvolvementSerializer(many=True, read_only=True)
    attachments = serializers.SerializerMethodField()
    acknowledgement_deadline = serializers.SerializerMethodField()
    signatures = serializers.SerializerMethodField()
    tracker = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()
    my_participation = serializers.SerializerMethodField()
    is_read_only = serializers.BooleanField(read_only=True)

    # Capability flags. The UI renders its action bar purely from these.
    can_edit = serializers.SerializerMethodField()
    can_manage_participants = serializers.SerializerMethodField()
    can_send_for_review = serializers.SerializerMethodField()
    can_return_review = serializers.SerializerMethodField()
    can_send_for_acknowledgement = serializers.SerializerMethodField()
    can_acknowledge = serializers.SerializerMethodField()
    can_remind_acknowledgements = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    can_export = serializers.SerializerMethodField()

    class Meta(MinuteListSerializer.Meta):
        fields = MinuteListSerializer.Meta.fields + [
            "agenda_body", "fro", "fro_name", "participants", "involvements",
            "attachments", "acknowledgement_deadline", "acknowledgement_due_days",
            "signatures", "tracker", "timeline", "my_participation",
            "is_read_only", "sent_for_review_at", "acknowledgement_opened_at",
            "updated_at",
            "can_edit", "can_manage_participants", "can_send_for_review",
            "can_return_review", "can_send_for_acknowledgement", "can_acknowledge",
            "can_remind_acknowledgements", "can_delete", "can_export",
        ]
        read_only_fields = fields

    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def get_attachments(self, obj):
        """
        Current versions only. Superseded rows are reachable through each row's
        `versions` list, so the panel shows one entry per document rather than every
        revision ever uploaded. Filtered in Python over the prefetched set.
        """
        current = [a for a in obj.attachments.all() if a.is_current]
        return MinuteAttachmentSerializer(
            current, many=True, context=self.context).data

    def get_acknowledgement_deadline(self, obj):
        return workflow.acknowledgement_deadline_summary(obj)

    def get_signatures(self, obj):
        return workflow.signature_blocks(obj)

    def get_tracker(self, obj):
        return workflow.build_tracker(obj)

    def get_timeline(self, obj):
        return workflow.build_timeline(obj)

    def get_my_participation(self, obj):
        """The caller's own row, so the UI can say "you have acknowledged"."""
        user = self._user()
        if user is None or not user.is_authenticated:
            return None
        row = next((p for p in obj.participants.all()
                    if p.user_id == user.id), None)
        if row is None:
            return None
        return MinuteParticipantSerializer(row, context=self.context).data

    def get_can_edit(self, obj):
        return perms.can_edit(self._user(), obj)

    def get_can_manage_participants(self, obj):
        return perms.can_manage_participants(self._user(), obj)

    def get_can_send_for_review(self, obj):
        return perms.can_send_for_review(self._user(), obj)

    def get_can_return_review(self, obj):
        return perms.can_return_review(self._user(), obj)

    def get_can_send_for_acknowledgement(self, obj):
        return perms.can_send_for_acknowledgement(self._user(), obj)

    def get_can_acknowledge(self, obj):
        return perms.can_acknowledge(self._user(), obj)

    def get_can_remind_acknowledgements(self, obj):
        return perms.can_remind_acknowledgements(self._user(), obj)

    def get_can_delete(self, obj):
        return perms.can_delete(self._user(), obj)

    def get_can_export(self, obj):
        return perms.can_view(self._user(), obj)
