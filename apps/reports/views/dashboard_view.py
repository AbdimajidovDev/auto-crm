from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from apps.reports.services import ReportService
from apps.reports.serializers import DashboardReportSerializer


@extend_schema(
    tags=['dashboard'],
    summary='Dashboard uchun Hisobot',
)
class DashboardReportAPIView(APIView):
    def get(self, request):
        data = ReportService.get_dashboard_data()
        serializer = DashboardReportSerializer(data)
        return Response(serializer.data)