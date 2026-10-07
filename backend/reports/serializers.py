from rest_framework import serializers

from .models import REPORT_FORMATS, ReportRun, ReportType, ScheduledReport


class ReportRunSerializer(serializers.ModelSerializer):
    report_type_display = serializers.CharField(source="get_report_type_display", read_only=True)
    requested_by_name = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()

    def get_file_url(self, obj):
        # Signed, expiring download URL — never a raw /media/ path.
        if not obj.file:
            return None
        from documents.protected_media import signed_media_url
        # Phase 11 (M7): bound to the requesting user, so a leaked link is
        # useless to anyone else. `request` is None only when a serializer is
        # used outside a request (email delivery), where an unbound link is
        # the deliberate fallback.
        viewer = getattr(self.context.get("request"), "user", None)
        return signed_media_url(obj.file.name, ttl=300, download=True,
                                user=viewer)

    class Meta:
        model = ReportRun
        fields = [
            "id", "report_type", "report_type_display", "params", "status",
            "requested_by", "requested_by_name", "file_url", "error",
            "created_at", "completed_at",
        ]
        read_only_fields = fields

    def get_requested_by_name(self, obj):
        return obj.requested_by.get_full_name() if obj.requested_by else None


class ReportRequestSerializer(serializers.Serializer):
    report_type = serializers.ChoiceField(choices=ReportType.choices)
    params = serializers.DictField(required=False, default=dict)

    def validate(self, attrs):
        fmt = (attrs.get("params") or {}).get("format")
        allowed = REPORT_FORMATS[attrs["report_type"]]
        if fmt and fmt not in allowed:
            raise serializers.ValidationError(
                {"format": f"{attrs['report_type']} supports: {', '.join(allowed)}."}
            )
        return attrs


class ScheduledReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduledReport
        fields = [
            "id", "report_type", "params", "recipients", "cron_expression",
            "is_active", "last_run_at", "created_by", "created_at",
        ]
        read_only_fields = ["id", "last_run_at", "created_by", "created_at"]
