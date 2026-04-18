from datetime import datetime

from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser, IsAuthenticated

from apps.reports.services.excel_export_service import ExcelExportService
from apps.reports.services.report_service import ReportService


@extend_schema(
    tags=['Reports'],
    summary='Hisobotni Exel formatda yuklash.',
)
class ReportsExcelExportAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):

        data = ReportService.get(request.user, request.GET)

        file = ExcelExportService.generate_report(data)

        response = HttpResponse(
            file,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

        filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

        response['Content-Disposition'] = f'attachment; filename={filename}'

        return response
