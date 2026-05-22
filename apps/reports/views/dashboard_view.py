from apps.reports.services import KPIService, TopPartsService, LowStockService, RecentSalesService, DateRangeResolver, \
    ChartService

from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.openapi import OpenApiTypes
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView


VALID_PERIODS = ("weekly", "monthly", "yearly")


@extend_schema(
    tags=["Dashboard"],
    summary="Dashboard — KPI, top mahsulotlar, kam qolgan tovar, oxirgi sotuvlar, grafik.",
    parameters=[
        OpenApiParameter(
            "period",
            OpenApiTypes.STR,
            description="Davr: weekly | monthly | yearly  (default: monthly)",
        ),
        OpenApiParameter(
            "store_id",
            OpenApiTypes.STR,
            description="Do'kon filtri: 'all' yoki do'kon ID raqami (default: all)",
        ),
    ],
)
class DashboardAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        period   = request.query_params.get("period",   "weekly")
        store_id = request.query_params.get("store_id", "all")

        if period not in VALID_PERIODS:
            return Response(
                {"detail": f"period qiymati noto'g'ri. To'g'ri qiymatlar: {', '.join(VALID_PERIODS)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        dr = DateRangeResolver.resolve(period)

        # Har bir service mustaqil — parallel qilish mumkin (kelajak uchun async)
        kpi          = KPIService.get(store_id, dr)
        top_parts    = TopPartsService.get(store_id, dr)
        low_stock    = LowStockService.get(store_id)
        recent_sales = RecentSalesService.get(store_id)
        chart        = ChartService.get(store_id, dr, period)

        return Response({
            "kpi":         kpi,
            "topParts":    top_parts,
            "lowStock":    low_stock,
            "recentSales": recent_sales,
            "chart":       chart,
        })
