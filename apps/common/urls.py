from django.urls import path
from apps.common.views import (
    AsyncJobStatusAPIView,
    AsyncJobCancelAPIView,
    AsyncJobDownloadAPIView,
)

urlpatterns = [
    path("<uuid:job_id>/status/", AsyncJobStatusAPIView.as_view(), name="async_job_status"),
    path("<uuid:job_id>/cancel/", AsyncJobCancelAPIView.as_view(), name="async_job_cancel"),
    path("<uuid:job_id>/download/", AsyncJobDownloadAPIView.as_view(), name="async_job_download"),
]
