from __future__ import annotations

from django.db.models import F, OuterRef, Subquery, IntegerField, Sum
from django.db.models.functions import Coalesce
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics, permissions, serializers
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from drf_spectacular.types import OpenApiTypes

from apps.inventory.filters import InventoryCountFilter
from apps.inventory.models import InventoryCount, InventorySession
from apps.inventory.serializers.inventory_count_serializer import InventoryCountSerializer
from apps.products.models import ProductBatch
from apps.common.paginations import StandardPagination



# ═══════════════════════════════════════════════
# BASE VIEW
# ═══════════════════════════════════════════════
from apps.inventory.services.inventory_count_service import InventoryResultService


class _InventoryResultBaseView(generics.ListAPIView):
    """
    Ko'p va kam chiqqan viewlar uchun umumiy klass.
    DRY: pagination, filter, search, permission — bir joyda.

    Subklasslar faqat get_queryset() ni override qiladi.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = InventoryCountSerializer
    pagination_class = StandardPagination

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = InventoryCountFilter
    search_fields = ["product__name"]
    ordering_fields = ["diff", "counted_quantity", "system_quantity", "created_at"]
    ordering = ["-diff"]

    def _get_session(self) -> InventorySession:
        """
        URL dan session_id ni oladi, mavjud emasligini tekshiradi.
        Subklasslarda takrorlanmaydi.
        """
        return get_object_or_404(
            InventorySession,
            pk=self.kwargs["session_id"]
        )


# ═══════════════════════════════════════════════
# VIEWS
# ═══════════════════════════════════════════════

@extend_schema_view(
    get=extend_schema(
        tags=["Inventory"],
        summary="Ko'p chiqqan mahsulotlar ro'yxati (ortiqcha inventar)",
        description=(
                "Inventarizatsiya natijasida tizim qoldig'idan KO'P chiqqan "
                "mahsulotlar. `diff` = counted_quantity − system_quantity (musbat)."
        ),
        parameters=[
            OpenApiParameter("session_id", OpenApiTypes.INT, location="path",
                             description="Inventarizatsiya sessiya ID"),
            OpenApiParameter("search", OpenApiTypes.STR, description="Mahsulot nomi bo'yicha qidirish"),
            OpenApiParameter("category", OpenApiTypes.INT, description="Kategoriya ID"),
            OpenApiParameter("is_check", OpenApiTypes.BOOL, description="Tekshirilgan holati"),
            OpenApiParameter("ordering", OpenApiTypes.STR, description="Tartiblash: diff, -diff, counted_quantity"),
            OpenApiParameter("page", OpenApiTypes.INT, description="Sahifa raqami"),
            OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni"),
        ],
    ),
)
class InventoryOverCountView(_InventoryResultBaseView):
    """
    Ko'p chiqqan mahsulotlar — status = 'm' (MORE).

    Logika serviceda, view faqat:
      1. session ni oladi
      2. servicega beradi
      3. natijani qaytaradi
    """

    def get_queryset(self):
        session = self._get_session()
        return InventoryResultService.over_counts(session.pk)


@extend_schema_view(
    get=extend_schema(
        tags=["Inventory"],
        summary="Kam chiqqan mahsulotlar ro'yxati (taqchillik)",
        description=(
                "Inventarizatsiya natijasida tizim qoldig'idan KAM chiqqan "
                "mahsulotlar. `diff` = counted_quantity − system_quantity (manfiy)."
        ),
        parameters=[
            OpenApiParameter("session_id", OpenApiTypes.INT, location="path",
                             description="Inventarizatsiya sessiya ID"),
            OpenApiParameter("search", OpenApiTypes.STR, description="Mahsulot nomi bo'yicha qidirish"),
            OpenApiParameter("category", OpenApiTypes.INT, description="Kategoriya ID"),
            OpenApiParameter("is_check", OpenApiTypes.BOOL, description="Tekshirilgan holati"),
            OpenApiParameter("ordering", OpenApiTypes.STR, description="Tartiblash: diff, -diff, counted_quantity"),
            OpenApiParameter("page", OpenApiTypes.INT, description="Sahifa raqami"),
            OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni"),
        ],
    ),
)
class InventoryShortCountView(_InventoryResultBaseView):
    """
    Kam chiqqan mahsulotlar — status = 'l' (LESS).
    """

    def get_queryset(self):
        session = self._get_session()
        return InventoryResultService.short_counts(session.pk)
