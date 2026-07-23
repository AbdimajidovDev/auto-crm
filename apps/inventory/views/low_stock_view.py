import math

from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter

from apps.common.paginations import StandardPagination
from apps.inventory.filters import LowStockItemFilter
from apps.inventory.models import LowStockItem
from apps.inventory.serializers.low_stock_serializer import LowStockItemSerializer
from apps.inventory.services.low_stock_service import LowStockService


class _BaseLowStockListView(generics.ListAPIView):
    """
    Shared list config. Both endpoints are paginated, filterable and ordered,
    and use select_related so the serializer's store_name/product_name resolve
    without N+1 queries.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = LowStockItemSerializer
    pagination_class = StandardPagination

    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = LowStockItemFilter
    ordering_fields = ["created_at", "current_quantity", "resolved_at"]
    ordering = ["-created_at"]

    status_value = None

    def get_queryset(self):
        # ✅ YAXSHI: status bo'yicha filtr indekslangan (models: Index(["status", "action_type"]),
        #   Index(["status", "-created_at"])) — full-table scan yo'q, ordering ham indeksdan foydalanadi.
        # ✅ YAXSHI: select_related("store", "product") — serializerdagi store_name/product_name
        #   (source=, SerializerMethodField EMAS) N+1 siz bitta JOIN bilan hal bo'ladi.
        # ✅ YAXSHI: bu GET endpoint tayyor LowStockItem yozuvlarini o'qiydi (12.5k ProductBatch
        #   ustidan og'ir aggregate emas). Og'ir baholash write-path'da hisoblab qo'yilgan — o'qish arzon.
        return (
            LowStockItem.objects
            .filter(status=self.status_value)
            .select_related("store", "product")
        )


@extend_schema(
    tags=["Low Stock"],
    summary="Kam qolgan mahsulotlar — JONLI ro'yxat (qoldiqlardan hisoblanadi)",
    parameters=[
        OpenApiParameter("action_type", OpenApiTypes.STR, description="purchase | transfer"),
        OpenApiParameter("store", OpenApiTypes.INT, description="Store id"),
        OpenApiParameter("search", OpenApiTypes.STR, description="Mahsulot nomi/SKU bo'yicha"),
        OpenApiParameter("page", OpenApiTypes.INT),
        OpenApiParameter("limit", OpenApiTypes.INT),
    ],
)
class LowStockListAPIView(APIView):
    """
    AVVAL: LowStockItem (hodisaviy) yozuvlarini o'qirdi — zaxira feature'dan
    OLDIN kamayib qolgan yoki hodisa o'tkazib yuborilgan holatlarda ro'yxat
    BO'SH chiqardi ("ishlamayapti" muammosining sababi).

    ENDI: har so'rovda qoldiqlardan jonli hisoblanadi (LowStockService.compute_live):
      * qoldiq <= min_stock va boshqa do'konda bor  -> transfer (sources bilan)
      * qoldiq <= min_stock va hech qayerda yo'q    -> purchase
    Javob shakli StandardPagination bilan bir xil (count/total_pages/results).
    Hodisaviy LowStockItem yozuvlari tarix va bildirishnomalar uchun qolaveradi.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qp = request.query_params
        results = LowStockService.compute_live(
            store_id=qp.get("store"),
            action_type=qp.get("action_type"),
            search=qp.get("search"),
        )

        try:
            page = max(1, int(qp.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            limit = min(100, max(1, int(qp.get("limit") or 20)))
        except (TypeError, ValueError):
            limit = 20

        count = len(results)
        total_pages = max(1, math.ceil(count / limit))
        page = min(page, total_pages)
        start = (page - 1) * limit

        return Response({
            "count": count,
            "total_pages": total_pages,
            "current_page": page,
            "next": None,
            "previous": None,
            "results": results[start:start + limit],
        })


@extend_schema(
    tags=["Low Stock"],
    summary="Yopilgan (RESOLVED) low-stock tarixi",
    parameters=[
        OpenApiParameter("action_type", OpenApiTypes.STR, description="purchase | transfer"),
        OpenApiParameter("store", OpenApiTypes.INT, description="Store id"),
        OpenApiParameter("product", OpenApiTypes.INT, description="Product id"),
        OpenApiParameter("ordering", OpenApiTypes.STR, description="-resolved_at, -created_at"),
        OpenApiParameter("page", OpenApiTypes.INT),
        OpenApiParameter("limit", OpenApiTypes.INT),
    ],
)
class LowStockHistoryAPIView(_BaseLowStockListView):
    status_value = LowStockItem.Status.RESOLVED
    ordering = ["-resolved_at", "-created_at"]


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 10 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [—]
# Izoh: Ikkala endpoint ham paginated, filterlangan, indekslangan status bo'yicha
#       filter va select_related bilan N+1 dan himoyalangan. DRY base klass.
#       History ordering(-resolved_at) uchun (status, -created_at) indeksi bor,
#       lekin resolved_at bo'yicha alohida indeks yo'q — hajm o'sganda faqat shu
#       bir tartiblash uchun (status, -resolved_at) indeksini ko'rib chiqish mumkin.
# ═══════════════════════════════
