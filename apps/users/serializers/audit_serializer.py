from rest_framework import serializers

from apps.users.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = (
            "id",
            "user",
            "user_display",
            "module",
            "action",
            "method",
            "path",
            "object_id",
            "status_code",
            "details",
            "ip_address",
            "user_agent",
            "created_at",
        )
