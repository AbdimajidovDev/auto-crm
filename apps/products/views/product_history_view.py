"""
Mahsulot tarixi (harakatlar lentasi) va statistikasi — bitta mahsulot uchun.

GET /api/products/<pk>/history/
    ?date_from=YYYY-MM-DD & date_to=YYYY-MM-DD   — sana oralig'i (ixtiyoriy)
    &store=<id>                                   — do'kon kesimi (ixtiyoriy)
    &type=entry|transfer|sale|sale_return|entry_return|writeoff|inventory
    &page=1&limit=50                              — hodisalar sahifasi

Ruxsat: autentifikatsiya + do'kon scoping (xodim faqat o'z do'konlari
yozuvlarini ko'radi — apps/common/store_scope.py).
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Product
from apps.products.serializers.product_crud_serializer import ProductDetailSerializer
from apps.products.services.product_history_service import (
    ProductHistoryService,
    parse_date_param,
)

EVENT_TYPES = {
    "field_change",
    "import",
    "writeoff",
    "adjustment",
    "inventory",
}


@extend_schema(
    tags=["Product"],
    summary="Mahsulot tarixi: master-data o'zgarishlari, import, hisobdan chiqarish, tuzatishlar",
    parameters=[
        OpenApiParameter("date_from", OpenApiTypes.DATE, description="Boshlanish sanasi"),
        OpenApiParameter("date_to", OpenApiTypes.DATE, description="Tugash sanasi"),
        OpenApiParameter("store", OpenApiTypes.INT, description="Do'kon ID bo'yicha filtr"),
        OpenApiParameter(
            "type",
            OpenApiTypes.STR,
            description="Hodisa turi: field_change, import, writeoff, adjustment, inventory",
        ),
        OpenApiParameter("page", OpenApiTypes.INT, description="Hodisalar sahifasi"),
        OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi hodisalar (max 200)"),
    ],
)
class ProductHistoryAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        product = get_object_or_404(
            Product.objects
            .select_related("category", "brand", "unit_measurement")
            .prefetch_related(
                "images",
                # Narx/qoldiq kartochkasi uchun — do'kon nomi bilan (N+1 yo'q)
                "batches__store",
                "batches__location",
            ),
            pk=pk,
        )

        service = ProductHistoryService(
            product,
            request.user,
            date_from=parse_date_param(request.query_params.get("date_from")),
            date_to=parse_date_param(request.query_params.get("date_to"), end_of_day=True),
            store_id=request.query_params.get("store"),
        )

        event_type = request.query_params.get("type")
        if event_type not in EVENT_TYPES:
            event_type = None

        data = service.build(
            page=_int_param(request.query_params.get("page"), default=1),
            limit=_int_param(request.query_params.get("limit"), default=50),
            event_type=event_type,
        )

        # Mahsulotning o'zi ham qaytariladi — tarix sahifasi qo'shimcha
        # so'rovsiz sarlavha (nom, SKU, rasm, qoldiq) ko'rsatishi uchun
        data["product"] = ProductDetailSerializer(
            product,
            context={"request": request, "all_stores": service.stores},
        ).data

        return Response(data, status=200)


def _int_param(value, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
