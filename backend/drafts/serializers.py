"""Serializers for the draft autosave API (Phase 111)."""
from rest_framework import serializers

from .models import DocumentDraft, DocumentDraftVersion


class DraftVersionSerializer(serializers.ModelSerializer):
    """A retained milestone, listed for the version-history panel."""
    saved_by_name = serializers.SerializerMethodField()
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)

    class Meta:
        model = DocumentDraftVersion
        fields = ["id", "version", "reason", "reason_label",
                  "saved_at", "saved_by_name"]

    def get_saved_by_name(self, obj):
        user = obj.saved_by
        if not user:
            return ""
        return user.get_full_name() or user.get_username()


class DraftVersionDetailSerializer(DraftVersionSerializer):
    """One milestone including its content, for previewing before restoring."""

    class Meta(DraftVersionSerializer.Meta):
        fields = DraftVersionSerializer.Meta.fields + ["payload"]


class DraftSerializer(serializers.ModelSerializer):
    """
    The rolling snapshot.

    `payload` is echoed verbatim. The server never inspects it — see the module
    docstring on models.DocumentDraft for why validation here would defeat the
    purpose of autosave.
    """
    versions = DraftVersionSerializer(many=True, read_only=True)
    saved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = DocumentDraft
        fields = ["id", "kind", "document_key", "payload", "version",
                  "autosave_count", "device_label", "saved_at", "created_at",
                  "saved_by_name", "versions"]
        read_only_fields = fields

    def get_saved_by_name(self, obj):
        return obj.owner.get_full_name() or obj.owner.get_username()


class DraftSummarySerializer(serializers.ModelSerializer):
    """
    Row shape for "you have unfinished work", which needs to say what the
    document is without shipping its whole body to a list screen.

    The title is read out of the payload rather than stored alongside it,
    because the payload is the single source of truth for a snapshot and a
    denormalised copy would go stale on the next autosave.
    """
    title = serializers.SerializerMethodField()

    class Meta:
        model = DocumentDraft
        fields = ["id", "kind", "document_key", "version", "saved_at",
                  "device_label", "title"]

    def get_title(self, obj):
        payload = obj.payload if isinstance(obj.payload, dict) else {}
        form = payload.get("form") if isinstance(payload.get("form"), dict) else {}
        for key in ("subject", "title", "to_line"):
            value = form.get(key) or payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:160]
        return ""


class DraftWriteSerializer(serializers.Serializer):
    """
    Inbound autosave.

    `payload` is any JSON object. `milestone` opts this write into the retained
    version history; an ordinary idle autosave omits it and simply overwrites.
    """
    payload = serializers.JSONField()
    milestone = serializers.ChoiceField(
        choices=DocumentDraftVersion.Reason.choices, required=False,
        allow_null=True, allow_blank=True)
    device_label = serializers.CharField(
        required=False, allow_blank=True, max_length=120)

    def validate_payload(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("The snapshot must be an object.")
        return value


class RestoreSerializer(serializers.Serializer):
    """Restore a retained milestone into the live snapshot."""
    version = serializers.IntegerField(min_value=1)
