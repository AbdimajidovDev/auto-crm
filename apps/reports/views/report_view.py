from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from apps.reports.permissions import scope_report_params
from apps.reports.services.report_service import ReportService


# ─────────────────────────────────────────────────────────────────────────────
#  FRONTEND UCHUN JAVOB DOKUMENTATSIYASI
#
#  GET /reports/  — barcha bloklar bitta javobda qaytadi. Query paramlar
#  (filter, store_id, from, to) HAMMA bloklarga bir xil ta'sir qiladi.
#
#  supplierStatistics (YANGI blok) — ta'minotchi kirim/qarz ko'rsatkichlari,
#  hisobot filtriga to'liq bo'ysunadi:
#    • supplierCount        (int)     — davrda kirim qilingan noyob ta'minotchilar soni
#    • distinctProductCount (int)     — davrda olingan noyob (xil) mahsulotlar soni
#    • totalPurchaseAmount  (decimal) — davrda olingan tovarlar umumiy summasi (so'm)
#    • totalDebt            (decimal) — davr ichidagi SOF qarz = kirim(in) − to'lov(pay).
#                                       DIQQAT: davr davomida ko'p to'langan bo'lsa
#                                       manfiy ham bo'lishi mumkin (haqiddan oshiq to'lov).
# ─────────────────────────────────────────────────────────────────────────────
_RESPONSE_EXAMPLE = OpenApiExample(
    "Namuna javob",
    value={
        "summary": {
            "totalRevenue": "15000000.00",
            "totalProfit": "4500000.00",
            "totalExpenses": "10500000.00",
            "totalOrders": 320,
            "averageOrderValue": "46875.00",
            "totalCustomers": 145,
        },
        "branchStatistics": [
            {"store_id": 1, "store__name": "Markaziy do'kon",
             "revenue": "9000000.00", "orders": 200, "customers": 90},
        ],
        "categoryStatistics": [
            {"categoryName": "Ehtiyot qismlar", "revenue": "8000000.00", "percent": 53.3},
        ],
        "topSellingProducts": [
            {"rank": 1, "productId": 12, "name": "Moy filtri",
             "category": "Ehtiyot qismlar", "totalSold": 150, "totalRevenue": "3000000.00"},
        ],
        "paymentStructure": [
            {"method": "Naqd", "type": "cash", "amount": "9000000.00", "percent": 60.0},
        ],
        "cardBreakdown": [
            {"name": "Uzcard", "amount": "4000000.00"},
        ],
        "expenses": [
            {"method": "cash", "amount": "2000000.00"},
        ],
        "supplierStatistics": {
            "supplierCount": 8,
            "distinctProductCount": 42,
            "totalPurchaseAmount": "11000000.00",
            "totalDebt": "2000000.00",
        },
        "debts": {
            "customerDebts": [
                {"customerName": "Ali Valiyev", "phone": "+998901234567", "debt": "250000.00"},
            ],
            "supplierDebts": [
                {"supplierName": "AutoParts LLC", "debt": "1200000.00"},
            ],
        },
    },
    response_only=True,
)


@extend_schema(
    tags=["Reports"],
    summary="Hisobot — summary, filiallar, kategoriyalar, to'lov tuzilmasi, ta'minotchi statistikasi, qarzlar.",
    description=(
        "Barcha hisobot bloklari bitta javobda. Query paramlar (filter, store_id, "
        "from, to) hamma bloklarga bir xil ta'sir qiladi.\n\n"
        "**supplierStatistics** (yangi):\n"
        "- `supplierCount` — davrda kirim qilingan noyob ta'minotchilar soni\n"
        "- `distinctProductCount` — davrda olingan noyob (xil) mahsulotlar soni\n"
        "- `totalPurchaseAmount` — davrda olingan tovarlar umumiy summasi\n"
        "- `totalDebt` — davr ichidagi sof qarz (kirim − to'lov); manfiy bo'lishi mumkin"
    ),
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
    responses={
        200: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description="To'liq hisobot obyekti (barcha bloklar bilan).",
            examples=[_RESPONSE_EXAMPLE],
        ),
    },
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
            # Do'kon admini faqat o'z do'koni bo'yicha ko'radi (superadmin — istalgan/umumiy)
            data = ReportService.get(scope_report_params(request))
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
