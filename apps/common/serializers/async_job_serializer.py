from rest_framework import serializers
from apps.common.models import AsyncJob


class AsyncJobSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = AsyncJob
        fields = [
            "id",
            "job_type",
            "status",
            "progress",
            "store",
            "created_by",
            "payload",
            "result",
            "error_message",
            "download_url",
            "created_at",
            "started_at",
            "completed_at",
        ]
        read_only_fields = fields

    def get_download_url(self, obj):
        if obj.output_file:
            return f"/api/v1/jobs/{obj.id}/download/"
        return None
