from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from apps.reports.services.report_service import ReportService


@extend_schema(
    tags=["Reports"],
    summary="Hisobot — summary, filiallar, kategoriyalar, to'lov tuzilmasi, qarzlar.",
    parameters=[
        OpenApiParameter(
            "filter", OpenApiTypes.STR,
            description="Davr: weekly | monthly | yearly  (default: monthly)",
        ),
        OpenApiParameter(
            "store_id", OpenApiTypes.STR,
            description="Do'kon filtri: 'all' yoki do'kon ID si (default: all)",
        ),
        OpenApiParameter(
            "from", OpenApiTypes.DATE,
            description="Boshlanish sanasi: YYYY-MM-DD (ixtiyoriy)",
        ),
        OpenApiParameter(
            "to", OpenApiTypes.DATE,
            description="Tugash sanasi: YYYY-MM-DD (ixtiyoriy)",
        ),
    ],
)
class ReportsAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        try:
            # ✅ YAXSHI: Butun hisobot ReportService orqali 60s cache bilan qaytariladi
            # (report_service.py'da cache.get/set) — takroriy og'ir aggregate'lar oldi olingan.
            # ⚠️ MUAMMO [PERF]: Javob ichidagi debts.customerDebts va debts.supplierDebts
            # ro'yxatlari PAGINATIONSIZ va LIMITSIZ qaytadi. DebtService har bir customer/supplier
            # bo'yicha guruhlab, musbat qarzi borlarini Python'da filtrlaydi — mijozlar/taminotchilar
            # ko'payganda (minglab) bitta requestda hammasi seriyalizatsiya qilinadi.
            # Natija: javob hajmi va vaqti chiziqli o'sadi, katta bazada sekinlashadi.
            # ✅ YECHIM: qarzlar ro'yxatini alohida paginatsiyalangan endpointga chiqarish
            # (StandardPagination) yoki eng bo'lmaganda DB darajasida .filter(...).order_by()[:N]
            # bilan cheklash.
            data = ReportService.get(request.query_params)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)
        return Response(data)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1  (debts ro'yxatlari paginationsiz/limitsiz qaytadi)
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [customerDebts/supplierDebts ro'yxatlarini paginatsiya yoki DB-limit bilan cheklash]
# ═══════════════════════════════
