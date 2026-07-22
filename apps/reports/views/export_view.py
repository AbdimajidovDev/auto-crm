from datetime import datetime

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from apps.reports.services.excel_export_service import ExcelExportService
from apps.reports.services.report_service import ReportFilterService, ReportService
from apps.store.models import Store

FILTER_LABELS = {
    "daily":   "Kunlik",
    "weekly":  "Haftalik",
    "monthly": "Oylik",
    "yearly":  "Yillik",
}


@extend_schema(
    tags=['Reports'],
    summary='Hisobotni Excel formatda yuklash (dashboard, diagrammalar bilan).',
)
class ReportsExcelExportAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # report_view.py bilan bir xil chaqiruv — sahifadagi filtrlar
        # (filter/store_id/from/to) aynan qo'llanadi
        data = ReportService.get(request.GET)

        # Excel sarlavhasi uchun meta: davr, do'kon, yaratilgan vaqt
        filter_type = request.GET.get("filter", "monthly")
        date_from, date_to = ReportFilterService.resolve_dates(
            filter_type, request.GET.get("from"), request.GET.get("to")
        )
        store_id = ReportFilterService.resolve_store(request.GET.get("store_id"))
        store_name = "Barcha do'konlar"
        if store_id:
            store_name = (
                Store.objects.filter(id=store_id).values_list("name", flat=True).first()
                or f"Do'kon #{store_id}"
            )

        period = f"{date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}"
        label = FILTER_LABELS.get(filter_type)
        if label and not (request.GET.get("from") and request.GET.get("to")):
            period = f"{label} ({period})"

        meta = {
            "period": period,
            "store": store_name,
            "generated": timezone.localtime().strftime("%d.%m.%Y %H:%M"),
        }

        file = ExcelExportService.generate_report(data, meta)

        response = HttpResponse(
            file,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

        filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

        response['Content-Disposition'] = f'attachment; filename={filename}'

        return response
