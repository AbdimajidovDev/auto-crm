from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.paginations import StandardPagination
from apps.contract.models import StockEntry, StockEntryItem, Supplier, SupplierTransaction
from apps.contract.serializers.supplier_payment_serializer import SupplierPaymentListSerializer
from apps.contract.views.stock_entry_view import (
    _total_in_subquery,
    _total_paid_subquery,
    _total_ret_subquery,
)
from apps.products.models import ProductImage


@extend_schema(
    tags=["Supplier"],
    summary="Ta'minotchi bo'yicha umumiy statistika (detail sahifa dashboardi).",
)
class SupplierStatsAPIView(APIView):
    """
    Bitta ta'minotchining detail sahifasi uchun jamlanma ko'rsatkichlar.

    Kirimlar soni kichik (yuzlab) bo'lgani uchun to'langan/qisman/to'lanmagan
    hisoblari Python'da bitta values() so'rovidan chiqariladi — subquery
    annotatsiyalari ustidan murakkab SQL aggregate yozishga hojat yo'q.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)

        # Eslatma: SupplierTransaction "in" yozuvi faqat QARZ qismini saqlaydi
        # (kirim paytida darhol to'langani paid_amount'da). Shuning uchun:
        #   kirim holati:  qarz = total_in(txn) - total_paid(txn),
        #                  to'langan pul = paid_amount + total_paid(txn)
        entries = list(
            StockEntry.objects.filter(supplier=supplier)
            .annotate(
                total_in=_total_in_subquery(),
                total_paid=_total_paid_subquery(),
                total_ret=_total_ret_subquery(),
            )
            .values("id", "total_in", "total_paid", "total_ret", "paid_amount", "total_amount", "created_at")
        )

        paid_count = partial_count = unpaid_count = 0
        purchase_sum = paid_at_entry_sum = Decimal("0")
        first_entry_at = last_entry_at = None
        for entry in entries:
            txn_in = entry["total_in"] or Decimal("0")
            txn_paid = entry["total_paid"] or Decimal("0")
            txn_ret = entry["total_ret"] or Decimal("0")
            paid_at_entry = entry["paid_amount"] or Decimal("0")
            purchase_sum += entry["total_amount"] or Decimal("0")
            paid_at_entry_sum += paid_at_entry

            # Qaytimlar (ret) ham qarzni kamaytiradi
            entry_debt = txn_in - txn_paid - txn_ret
            if entry_debt <= 0:
                paid_count += 1
            elif paid_at_entry + txn_paid <= 0:
                unpaid_count += 1
            else:
                partial_count += 1

            created = entry["created_at"]
            if created:
                first_entry_at = created if first_entry_at is None else min(first_entry_at, created)
                last_entry_at = created if last_entry_at is None else max(last_entry_at, created)

        txn_totals = SupplierTransaction.objects.filter(supplier=supplier).aggregate(
            total_paid=Sum("amount", filter=Q(type=SupplierTransaction.TransactionType.PAYMENT)),
            total_in=Sum("amount", filter=Q(type=SupplierTransaction.TransactionType.INVENTORY_IN)),
            total_ret=Sum("amount", filter=Q(type=SupplierTransaction.TransactionType.RETURN)),
        )
        txn_paid_total = txn_totals["total_paid"] or Decimal("0")
        txn_in_total = txn_totals["total_in"] or Decimal("0")
        txn_ret_total = txn_totals["total_ret"] or Decimal("0")

        # Jami to'langan = kirim paytidagi to'lovlar + keyingi qarz to'lovlari
        total_paid = paid_at_entry_sum + txn_paid_total
        # Qarz = kirim (in) − to'lovlar (pay) − qaytimlar (ret)
        debt = txn_in_total - txn_paid_total - txn_ret_total

        items_total = StockEntryItem.objects.filter(entry__supplier=supplier).aggregate(
            quantity=Sum("quantity"),
        )["quantity"] or 0

        # Kirimlar chastotasi: birinchi kirimdan hozirgacha oyiga o'rtacha nechta kirim
        orders_per_month = 0.0
        if entries and first_entry_at:
            months = max((timezone.now() - first_entry_at).days / Decimal("30.44"), Decimal("1"))
            orders_per_month = float(round(Decimal(len(entries)) / months, 1))

        return Response(
            {
                "supplier_id": supplier.id,
                "created_at": supplier.created_at,
                "entries_count": len(entries),
                "paid_entries_count": paid_count,
                "partial_entries_count": partial_count,
                "unpaid_entries_count": unpaid_count,
                "total_purchase_amount": str(purchase_sum),
                "total_paid_amount": str(total_paid),
                "total_debt": str(debt if debt > 0 else Decimal("0")),
                # Balans: qarzdan ortiqcha to'langan summa (avans)
                "balance": str(-debt if debt < 0 else Decimal("0")),
                "items_total_quantity": items_total,
                "orders_per_month": orders_per_month,
                "first_entry_at": first_entry_at,
                "last_entry_at": last_entry_at,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Supplier"],
    summary="Ta'minotchining barcha to'lovlari (kirimlardan qat'i nazar).",
    parameters=[
        OpenApiParameter("page", OpenApiTypes.INT, description="Sahifa raqami"),
        OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni"),
    ],
)
class SupplierPaymentsBySupplierAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierPaymentListSerializer
    pagination_class = StandardPagination

    def get(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)
        qs = (
            SupplierTransaction.objects.filter(
                supplier=supplier, type=SupplierTransaction.TransactionType.PAYMENT
            )
            .select_related("bank_card")
            .order_by("-created_at")
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = self.serializer_class(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)


@extend_schema(
    tags=["Supplier"],
    summary="Ta'minotchidan kelgan tovarlar (kirimlar bo'yicha jamlangan).",
    parameters=[
        OpenApiParameter("search", OpenApiTypes.STR, description="Nomi, SKU yoki barkod bo'yicha qidirish"),
        OpenApiParameter("page", OpenApiTypes.INT, description="Sahifa raqami"),
        OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni"),
    ],
)
class SupplierProductsAPIView(APIView):
    """
    Ta'minotchi kirimlaridagi mahsulotlar: har mahsulot bo'yicha jami olingan
    miqdor, kirimlar soni va oxirgi kirim/sotish narxi.

    Ikki bosqich: (1) product bo'yicha GROUP BY + pagination, (2) faqat sahifadagi
    productlar uchun oxirgi narxlar va rasm — product_id__in bilan kichik so'rovlar.
    """

    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination

    def get(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)

        qs = (
            StockEntryItem.objects.filter(entry__supplier=supplier)
            .values(
                "product_id",
                "product__name",
                "product__sku",
                "product__barcode",
                "product__category__name",
            )
            .annotate(
                total_quantity=Sum("quantity"),
                entries_count=Count("entry_id", distinct=True),
                last_entry_at=Max("entry__created_at"),
            )
            .order_by("-last_entry_at")
        )

        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(product__name__icontains=search)
                | Q(product__sku__icontains=search)
                | Q(product__barcode__icontains=search)
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self) or []

        product_ids = [row["product_id"] for row in page]

        # Oxirgi kirimdagi narxlar: product bo'yicha eng yangi StockEntryItem
        last_prices: dict[int, dict] = {}
        price_rows = (
            StockEntryItem.objects.filter(entry__supplier=supplier, product_id__in=product_ids)
            .order_by("product_id", "-entry__created_at", "-id")
            .values("product_id", "purchase_price", "selling_price")
        )
        for row in price_rows:
            last_prices.setdefault(row["product_id"], row)

        images: dict[int, str] = {}
        image_rows = (
            ProductImage.objects.filter(product_id__in=product_ids)
            .order_by("product_id", "id")
            .values("product_id", "image")
        )
        for row in image_rows:
            if row["product_id"] not in images and row["image"]:
                images[row["product_id"]] = request.build_absolute_uri(
                    f"{settings.MEDIA_URL}{row['image']}"
                )

        results = [
            {
                "product": row["product_id"],
                "product_name": row["product__name"],
                "sku": row["product__sku"],
                "barcode": row["product__barcode"],
                "category_name": row["product__category__name"],
                "total_quantity": row["total_quantity"],
                "entries_count": row["entries_count"],
                "last_entry_at": row["last_entry_at"],
                "last_purchase_price": str(last_prices.get(row["product_id"], {}).get("purchase_price") or "0"),
                "last_selling_price": str(last_prices.get(row["product_id"], {}).get("selling_price") or "0"),
                "image": images.get(row["product_id"]),
            }
            for row in page
        ]

        return paginator.get_paginated_response(results)
