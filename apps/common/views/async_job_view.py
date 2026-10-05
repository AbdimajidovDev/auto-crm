from django.http import FileResponse, Http404
from django.core.exceptions import PermissionDenied, ValidationError
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.models import AsyncJob
from apps.common.serializers.async_job_serializer import AsyncJobSerializer
from apps.common.services.async_job_service import AsyncJobService


@extend_schema(
    tags=["Async Jobs"],
    summary="Fon vazifasi (job) holatini tekshirish",
    responses={200: AsyncJobSerializer},
)
class AsyncJobStatusAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, job_id):
        try:
            job = AsyncJobService.get_job(job_id, request.user)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_404_NOT_FOUND)
        except PermissionDenied as e:
            return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)

        serializer = AsyncJobSerializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Async Jobs"],
    summary="Fon vazifasini bekor qilish",
    responses={200: AsyncJobSerializer},
)
class AsyncJobCancelAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, job_id):
        try:
            job = AsyncJobService.cancel_job(job_id, request.user)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except PermissionDenied as e:
            return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)

        serializer = AsyncJobSerializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Async Jobs"],
    summary="Fon vazifasi natijasi bo'lgan faylni yuklab olish",
    responses={200: OpenApiTypes.BINARY},
)
class AsyncJobDownloadAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, job_id):
        try:
            job = AsyncJobService.get_job(job_id, request.user)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_404_NOT_FOUND)
        except PermissionDenied as e:
            return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)

        if not job.output_file or not job.output_file.storage.exists(job.output_file.name):
            return Response({"detail": "Chiqish fayli topilmadi yoki hali tayyor emas."}, status=status.HTTP_404_NOT_FOUND)

        filename = job.output_file.name.split("/")[-1]
        return FileResponse(
            job.output_file.open("rb"),
            as_attachment=True,
            filename=filename,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
