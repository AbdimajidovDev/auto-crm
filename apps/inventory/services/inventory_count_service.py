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

from apps.inventory.models import InventoryCount, InventorySession
from apps.products.models import ProductBatch
from apps.common.paginations import StandardPagination


# ═══════════════════════════════════════════════
# SERVICE
# ═══════════════════════════════════════════════

class InventoryResultService:
    """
    Inventarizatsiya natijasi uchun DB logikasi.

    SRP: view da queryset yo'q — barcha DB ishi shu klassda.
    Testlash oson: view siz service ni alohida test qilish mumkin.

    Queryset strategiyasi:
      InventoryCount
        → select_related("product", "product__category", "product__unit_measurement")
           FK zanjiri — bitta JOIN, qo'shimcha SQL yo'q.
        → annotate(system_quantity)
           Tizimda mavjud bo'lgan umumiy qoldiq (ProductBatch.quantity Sum).
           Subquery ishlatiladi — annotate+JOIN kartezian ko'payish xavfini
           bartaraf etadi.
        → diff = counted_quantity - system_quantity
           Farq: musbat → ko'p chiqqan, manfiy → kam chiqqan.

    SQL soni:
      1 → InventoryCount + JOIN (product, category, unit_measurement)
      Subquery correlated — asosiy queryga qo'shiladi, alohida SQL emas.
      Jami: 1 SQL, count soni dan mustaqil.
    """

    @staticmethod
    def _base_queryset(session_id: int, status: str):
        """
        Berilgan session va status uchun optimallashtirilgan queryset.

        system_quantity:
          Shu mahsulotning shu do'kondagi barcha aktiv batch lardan
          umumiy qoldiq miqdori.
          Subquery orqali — kartezian xavfi yo'q.
        """
        system_qty_sq = Coalesce(
            Subquery(
                ProductBatch.objects
                .filter(
                    product=OuterRef("product_id"),
                    store=OuterRef("session__store_id"),
                    is_active=True,
                )
                .values("product")  # GROUP BY product
                .annotate(total=Sum("quantity"))
                .values("total")[:1],
                output_field=IntegerField(),
            ),
            0,
            output_field=IntegerField(),
        )

        return (
            InventoryCount.objects
            .filter(session_id=session_id, status=status)
            .select_related(
                "product",
                "product__category",
                "product__unit_measurement",
            )
            .annotate(system_quantity=system_qty_sq)
            .annotate(
                # diff: sanash natijasi − tizim qoldig'i
                # ko'p chiqqan (status='m') → musbat
                # kam chiqqan (status='l') → manfiy
                diff=F("counted_quantity") - F("system_quantity")
            )
            .order_by("-diff")  # eng katta farqdan boshlab
        )

    @classmethod
    def over_counts(cls, session_id: int):
        """Ko'p chiqqan mahsulotlar (counted > system → status='m')."""
        return cls._base_queryset(session_id, status=InventoryCount.Status.MORE)

    @classmethod
    def short_counts(cls, session_id: int):
        """Kam chiqqan mahsulotlar (counted < system → status='l')."""
        return cls._base_queryset(session_id, status=InventoryCount.Status.LESS)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [—]
# Izoh:
#   ✅ system_quantity correlated Subquery bilan hisoblanadi — annotate+JOIN kartezian
#      ko'payishidan himoya (Sale namunasidagi kabi to'g'ri strategiya).
#   ✅ select_related("product","product__category","product__unit_measurement") — serializerdagi
#      product_name/category_name/unit_measurement N+1 siz.
#   ✅ Natija view'da paginate qilingani uchun Subquery faqat sahifadagi qatorlar uchun ishlaydi.
#   ⚠️ Kichik [PERF]: InventoryCount(session, status) uchun alohida composite indeks yo'q
#      (unique_together (session, product) faqat session prefiksini beradi). Bir sessiyada
#      juda ko'p count bo'lsa status bo'yicha filtr indekssiz — hozirgi hajmda kritik emas.
# ═══════════════════════════════
