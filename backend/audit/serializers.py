from rest_framework import serializers
from .models import AuditLog

class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.get_full_name", read_only=True, default="")
    content_type_name = serializers.CharField(source="content_type.model", read_only=True, default="")

    class Meta:
        model = AuditLog
        fields = [
            "id", "actor", "actor_name", "action",
            "content_type_name", "object_id", "object_repr",
            "changes", "ip_address", "created_at",
        ]
        read_only_fields = fields
