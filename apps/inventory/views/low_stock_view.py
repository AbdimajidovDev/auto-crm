from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter

from apps.common.paginations import StandardPagination
from apps.inventory.filters import LowStockItemFilter
from apps.inventory.models import LowStockItem
from apps.inventory.serializers.low_stock_serializer import LowStockItemSerializer


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
    summary="Ochiq (OPEN) low-stock yozuvlari ro'yxati",
    parameters=[
        OpenApiParameter("action_type", OpenApiTypes.STR, description="purchase | transfer"),
        OpenApiParameter("store", OpenApiTypes.INT, description="Store id"),
        OpenApiParameter("product", OpenApiTypes.INT, description="Product id"),
        OpenApiParameter("ordering", OpenApiTypes.STR, description="-created_at, current_quantity"),
        OpenApiParameter("page", OpenApiTypes.INT),
        OpenApiParameter("limit", OpenApiTypes.INT),
    ],
)
class LowStockListAPIView(_BaseLowStockListView):
    status_value = LowStockItem.Status.OPEN


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
