from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.excel_export import parse_date_param
from apps.common.paginations import StandardPagination
from apps.common.store_scope import ensure_store_access, scope_queryset
from apps.inventory.models import StockAdjustment
from apps.inventory.serializers.stock_adjustment_serializer import (
    StockAdjustmentCreateSerializer,
    StockAdjustmentListSerializer,
)
from apps.inventory.services.stock_adjustment_service import StockAdjustmentService


class StockAdjustmentCreateAPIView(APIView):
    """POST /api/inventory/adjust/ — RBAC: inventory.create (middleware)."""

    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = StockAdjustmentCreateSerializer

    @extend_schema(
        tags=["Inventory"],
        summary="Bitta mahsulot qoldig'ini to'liq inventarizatsiyasiz to'g'irlash",
        request=StockAdjustmentCreateSerializer,
        responses={201: StockAdjustmentListSerializer},
    )
    def post(self, request):
        serializer = StockAdjustmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        ensure_store_access(request.user, data["store_id"])

        adjustment = StockAdjustmentService.adjust(
            store_id=data["store_id"],
            product_id=data["product_id"],
            new_quantity=data["new_quantity"],
            reason=data["reason"],
            comment=data.get("comment", ""),
            user=request.user,
        )

        detail = (
            StockAdjustment.objects
            .select_related("store", "product", "created_by")
            .get(pk=adjustment.pk)
        )
        return Response(
            StockAdjustmentListSerializer(detail).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["Inventory"],
    summary="Qoldiq to'g'irlashlari tarixi (store-scope, filtrlar, pagination)",
    parameters=[
        OpenApiParameter(name="store", description="Do'kon ID bo'yicha filtr", required=False, type=int),
        OpenApiParameter(name="product", description="Mahsulot ID bo'yicha filtr", required=False, type=int),
        OpenApiParameter(name="reason", description="Sabab bo'yicha filtr", required=False, type=str),
        OpenApiParameter(name="search", description="Mahsulot nomi/SKU/shtrix-kod bo'yicha qidiruv", required=False, type=str),
        OpenApiParameter(name="date_from", description="YYYY-MM-DD", required=False, type=str),
        OpenApiParameter(name="date_to", description="YYYY-MM-DD", required=False, type=str),
    ],
)
class StockAdjustmentListAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = StockAdjustmentListSerializer
    pagination_class = StandardPagination

    def get(self, request):
        qs = (
            StockAdjustment.objects
            .select_related("store", "product", "created_by")
        )
        qs = scope_queryset(qs, request.user)

        store = request.query_params.get("store")
        if store:
            qs = qs.filter(store_id=store)

        product = request.query_params.get("product")
        if product:
            qs = qs.filter(product_id=product)

        reason = request.query_params.get("reason")
        if reason:
            qs = qs.filter(reason=reason)

        search = (request.query_params.get("search") or "").strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(product__name__icontains=search)
                | Q(product__sku__icontains=search)
                | Q(product__barcode__icontains=search)
            )

        date_from = parse_date_param(request.query_params.get("date_from"))
        date_to = parse_date_param(request.query_params.get("date_to"))
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = StockAdjustmentListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
