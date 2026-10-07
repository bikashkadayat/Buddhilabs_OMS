from rest_framework import serializers

from .models import Category, Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id", "category", "category_label", "title", "body", "action_url",
            "is_read", "created_at", "read_at",
        ]
        read_only_fields = fields


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)

    class Meta:
        model = NotificationPreference
        fields = ["category", "category_label", "in_app_enabled", "email_enabled"]

    def validate_category(self, value):
        if value not in Category.values:
            raise serializers.ValidationError("Unknown category.")
        return value
