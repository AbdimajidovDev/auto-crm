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

        # ⚠️ MUAMMO [KRITIK]: ReportService.get signaturasi `get(params)` — bitta argument.
        # Bu yerda esa `get(request.user, request.GET)` — IKKITA positional argument beriladi.
        # Natija: runtime'da TypeError (get() takes 1 positional argument but 2 were given),
        # ya'ni Excel export endpoint umuman ishlamaydi (500).
        # ✅ YECHIM: report_view.py bilan bir xil chaqiruv:
        #   data = ReportService.get(request.GET)
        data = ReportService.get(request.user, request.GET)

        # ⚠️ MUAMMO [PERF]: Excel generatsiyasi debts.customerDebts / supplierDebts ro'yxatlari
        # ustidan Python loop bilan qator yozadi (excel_export_service.py). Bu ro'yxatlar
        # ReportService'da limitsiz — mijozlar/taminotchilar minglab bo'lsa, workbook to'liq
        # xotirada ({'in_memory': True}) quriladi va sinxron requestni uzoq bloklaydi.
        # ✅ YECHIM: eksportni Celery task'ga chiqarish (fayl tayyor bo'lgach yuklab olish),
        # yoki manba ro'yxatlarni DB darajasida cheklab/streaming yozish.
        file = ExcelExportService.generate_report(data)

        response = HttpResponse(
            file,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

        filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

        response['Content-Disposition'] = f'attachment; filename={filename}'

        return response


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1  (ReportService.get() 2 pozitsion argument bilan chaqirilgan — TypeError)
# Performance muammolari: 1  (sinxron in-memory Excel, limitsiz debts ro'yxatlari ustidan loop)
# Arxitektura muammolari: 1  (permission IsAuthenticated, report_view esa IsAdminUser — nomuvofiq huquq)
# Umumiy baho: 4 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [ReportService.get(request.GET) chaqiruvini tuzatish — endpoint hozir 500 beradi]
# ═══════════════════════════════
