"""
stock_entry_return_view.py — xarid (kirim) qaytimlari API'lari.

  POST /contract/entry/<id>/return/   — kirim bo'yicha qaytim yaratish
  GET  /contract/entry/<id>/returns/  — bitta kirimning qaytimlari
  GET  /contract/entry/returns/       — barcha qaytimlar (filtrli, paginatsiyali)
  GET  /contract/entry/returns/export/ — qaytimlarni Excel (.xlsx) ga eksport

Ruxsat: kirim yarata oladigan (stockentry.create) xodim o'z do'konining
kirimlarini qaytara oladi; superuser — hammasini.
"""
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.excel_export import BaseExcelExportAPIView
from apps.common.paginations import StandardPagination
from apps.contract.models import StockEntry, StockEntryReturn, StockEntryReturnItem
from apps.contract.permissions import CanCreateStockEntry, allowed_store_ids, ensure_store_access
from apps.contract.serializers import (
    StockEntryReturnCreateSerializer,
    StockEntryReturnListSerializer,
)
from apps.contract.services import StockEntryReturnService


def _returns_queryset(request):
    """
    Qaytimlar ro'yxati/eksporti uchun umumiy queryset:
    filtrlar (store, supplier, entry, date_from/date_to, search) + do'kon scoping.
    """
    qs = (
        StockEntryReturn.objects
        .select_related("entry__supplier", "entry__store", "created_by")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=StockEntryReturnItem.objects.select_related("product"),
            )
        )
        .order_by("-created_at")
    )

    params = request.query_params
    if params.get("store") and params["store"] != "all":
        qs = qs.filter(entry__store_id=params["store"])
    if params.get("supplier") and params["supplier"] != "all":
        qs = qs.filter(entry__supplier_id=params["supplier"])
    if params.get("entry"):
        qs = qs.filter(entry_id=params["entry"])
    search = (params.get("search") or "").strip()
    if search:
        qs = qs.filter(entry__supplier__name__icontains=search)

    # Do'kon xodimi faqat o'z do'kon(lar)ining qaytimlarini ko'radi
    allowed = allowed_store_ids(request.user)
    if allowed is not None:
        qs = qs.filter(entry__store_id__in=allowed)

    return qs


@extend_schema(
    tags=["Stock Entry"],
    summary="Xarid bo'yicha mahsulot qaytarish (qaytim yaratish)",
    request=StockEntryReturnCreateSerializer,
    responses={201: OpenApiTypes.OBJECT},
)
class StockEntryReturnCreateAPIView(APIView):
    permission_classes = [CanCreateStockEntry]

    def post(self, request, entry_id):
        entry = get_object_or_404(StockEntry, pk=entry_id)
        ensure_store_access(request.user, entry.store_id)

        serializer = StockEntryReturnCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            stock_return = StockEntryReturnService.create_return(
                entry=entry,
                items=data["items"],
                note=data["note"],
                user=request.user,
            )
        except ValidationError as e:
            detail = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return Response({"detail": detail}, status=400)

        return Response(
            {
                "id": stock_return.id,
                "entry": stock_return.entry_id,
                "total_amount": str(stock_return.total_amount),
                "debt_cancelled": str(stock_return.debt_cancelled),
                "refund_amount": str(stock_return.refund_amount),
                "items_count": len(data["items"]),
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["Stock Entry"],
    summary="Bitta xaridning qaytimlari ro'yxati",
    responses={200: StockEntryReturnListSerializer(many=True)},
)
class StockEntryReturnsByEntryAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, entry_id):
        entry = get_object_or_404(StockEntry, pk=entry_id)
        allowed = allowed_store_ids(request.user)
        if allowed is not None and entry.store_id not in allowed:
            return Response({"detail": "Ruxsat yo'q"}, status=403)

        qs = (
            StockEntryReturn.objects
            .filter(entry=entry)
            .select_related("entry__supplier", "entry__store", "created_by")
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=StockEntryReturnItem.objects.select_related("product"),
                )
            )
            .order_by("-created_at")
        )
        serializer = StockEntryReturnListSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data, status=200)


@extend_schema(
    tags=["Stock Entry"],
    summary="Barcha xarid qaytimlari ro'yxati",
    parameters=[
        OpenApiParameter("search", OpenApiTypes.STR, description="Ta'minotchi nomi bo'yicha qidirish"),
        OpenApiParameter("supplier", OpenApiTypes.INT, description="Ta'minotchi ID"),
        OpenApiParameter("store", OpenApiTypes.INT, description="Do'kon ID"),
        OpenApiParameter("entry", OpenApiTypes.INT, description="Xarid (kirim) ID"),
        OpenApiParameter("date_from", OpenApiTypes.DATE, description="Sana boshi (YYYY-MM-DD)"),
        OpenApiParameter("date_to", OpenApiTypes.DATE, description="Sana oxiri (YYYY-MM-DD)"),
        OpenApiParameter("page", OpenApiTypes.INT, description="Sahifa raqami"),
        OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni"),
    ],
    responses={200: StockEntryReturnListSerializer(many=True)},
)
class StockEntryReturnListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination

    def get(self, request):
        from apps.common.excel_export import parse_date_param

        qs = _returns_queryset(request)
        date_from = parse_date_param(request.query_params.get("date_from"))
        date_to = parse_date_param(request.query_params.get("date_to"))
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = StockEntryReturnListSerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)


@extend_schema(
    tags=["Stock Entry"],
    summary="Xarid qaytimlarini Excel (.xlsx) ga eksport qilish "
            "(?date_from=&date_to=&store=&supplier=&search= — ro'yxat filtrlari bilan bir xil)",
)
class StockEntryReturnExportAPIView(BaseExcelExportAPIView):
    filename = "xarid_qaytimlari"
    date_field = "created_at"

    def get_queryset(self, request):
        return _returns_queryset(request)

    def get_sheets(self, request, queryset):
        return_columns = [
            ("ID", 8),
            ("Sana", 17),
            ("Xarid ID", 10),
            ("Ta'minotchi", 26),
            ("Do'kon", 20),
            ("Qaytim summasi", 15),
            ("Qarzdan kamaydi", 15),
            ("Pul qaytimi", 15),
            ("Mahsulot xillari", 14),
            ("Jami dona", 12),
            ("Yaratdi", 24),
            ("Izoh", 34),
        ]
        item_columns = [
            ("Qaytim ID", 10),
            ("Xarid ID", 10),
            ("Sana", 17),
            ("Mahsulot", 40),
            ("SKU", 14),
            ("Miqdor", 10),
            ("Olish narxi", 14),
            ("Summa", 14),
        ]

        returns = list(queryset)

        def return_rows():
            for ret in returns:
                items = list(ret.items.all())
                yield [
                    ret.id,
                    ret.created_at,
                    ret.entry_id,
                    ret.entry.supplier.name if ret.entry.supplier else "",
                    ret.entry.store.name if ret.entry.store else "",
                    ret.total_amount,
                    ret.debt_cancelled,
                    ret.refund_amount,
                    len(items),
                    sum(item.quantity for item in items),
                    ret.created_by.full_name if ret.created_by else "",
                    ret.note or "",
                ]

        def item_rows():
            for ret in returns:
                for item in ret.items.all():
                    yield [
                        ret.id,
                        ret.entry_id,
                        ret.created_at,
                        item.product.name if item.product else "",
                        item.product.sku if item.product else "",
                        item.quantity,
                        item.purchase_price,
                        item.amount,
                    ]

        return [
            ("Qaytimlar", return_columns, return_rows()),
            ("Qaytim mahsulotlari", item_columns, item_rows()),
        ]
