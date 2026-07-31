"""
stock_entry_transfer_view.py — kirimdagi tovarlarni o'tkazmaga (transfer) ko'chirish.

  GET /contract/entry/<id>/transfer-precheck/  — kirim tovarlarini baza bilan solishtiradi

Bu endpoint HECH NARSA YOZMAYDI. U faqat kirimdagi har bir tovarni tekshiradi va
holatini qaytaradi, front esa sessiya yaratishdan OLDIN ogohlantirish oynasini
ko'rsatadi. Sessiyaning o'zi mavjud `POST /transfer/session/` orqali yaratiladi.

Holatlar:
  ok        — mahsulot faol va jo'natuvchi do'konda qoldig'i bor
  no_stock  — partiya bor, lekin qoldiq 0 (o'tkazib bo'lmaydi)
  no_batch  — jo'natuvchi do'konda bu mahsulot partiyasi umuman yo'q
  inactive  — mahsulot nofaol/qoralama (o'tkazma serializeri qabul qilmaydi)
  missing   — mahsulot bazada topilmadi (himoya uchun; FK PROTECT sababli kam uchraydi)
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contract.models import StockEntry, StockEntryItem
from apps.contract.permissions import ensure_store_access
from apps.products.models import Product, ProductBatch

# Front bu holatlarni chekka/qoralamaga umuman qo'sha olmaydi
BLOCKED_STATUSES = {"inactive", "missing"}


@extend_schema(
    tags=["StockEntry"],
    summary="Kirim tovarlarini o'tkazmaga ko'chirishdan oldingi tekshiruv",
    description=(
        "Kirimdagi har bir tovarni baza bilan solishtiradi: mahsulot mavjudmi, "
        "faolmi va jo'natuvchi do'konda qoldig'i bormi. Hech narsa yozmaydi."
    ),
    responses={200: OpenApiTypes.OBJECT},
)
class StockEntryTransferPrecheckAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, entry_id):
        entry = get_object_or_404(
            StockEntry.objects.select_related("store", "supplier"), pk=entry_id
        )
        # Xodim faqat o'z do'konining kirimini o'tkazmaga ko'chira oladi
        ensure_store_access(request.user, entry.store_id)

        # Kirim qatorlari: bir mahsulot bir necha qatorda kelishi mumkin —
        # o'tkazmada dublikat bo'lmasligi uchun mahsulot bo'yicha birlashtiriladi
        merged: dict[int, dict] = {}
        order: list[int] = []
        for item in StockEntryItem.objects.filter(entry=entry).select_related("product"):
            product_id = item.product_id
            existing = merged.get(product_id)
            if existing:
                existing["entry_quantity"] += item.quantity
                continue
            merged[product_id] = {
                "product": product_id,
                "name": item.product.name if item.product else "",
                "sku": (item.product.sku if item.product else "") or "",
                "barcode": (item.product.barcode if item.product else "") or "",
                "entry_quantity": item.quantity,
            }
            order.append(product_id)

        product_ids = list(merged.keys())

        # Faol mahsulotlar va do'kondagi partiyalar — har biri bitta so'rov (N+1 yo'q)
        existing_ids = set(
            Product.objects.filter(id__in=product_ids).values_list("id", flat=True)
        )
        active_ids = set(
            Product.objects.filter(
                id__in=product_ids, status=Product.ProductStatus.ACTIVE
            ).values_list("id", flat=True)
        )
        batches = dict(
            ProductBatch.objects.filter(
                store_id=entry.store_id, product_id__in=product_ids
            ).values_list("product_id", "quantity")
        )

        items = []
        for product_id in order:
            row = merged[product_id]
            if product_id not in existing_ids:
                row["status"] = "missing"
                row["available"] = 0
            elif product_id not in active_ids:
                row["status"] = "inactive"
                row["available"] = batches.get(product_id, 0)
            elif product_id not in batches:
                row["status"] = "no_batch"
                row["available"] = 0
            else:
                available = batches[product_id]
                row["available"] = available
                row["status"] = "ok" if available > 0 else "no_stock"
            items.append(row)

        ok_count = sum(1 for i in items if i["status"] == "ok")
        blocked = sum(1 for i in items if i["status"] in BLOCKED_STATUSES)
        return Response(
            {
                "entry": entry.id,
                "from_store": entry.store_id,
                "from_store_name": entry.store.name,
                "supplier_name": entry.supplier.name,
                "items": items,
                "summary": {
                    "total": len(items),
                    "ok": ok_count,
                    "blocked": blocked,
                    "warning": len(items) - ok_count - blocked,
                },
            },
            status=200,
        )
