from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.reports.services.report_service import ReportService
from django.core.cache import cache



@extend_schema(
    tags=["Reports"],
    summary="Hisobot paneli uchun hisobotlar",
)
class ReportsAPIView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):

        cache_key = f"report:{request.GET}"

        data = cache.get(cache_key)

        if not data:
            data = ReportService.get(request.user, request.GET)
            cache.set(cache_key, data, 60)

        return Response(data)
