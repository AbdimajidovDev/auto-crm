from django.shortcuts import get_object_or_404
from apps.inventory.models import InventorySession


from django.db.models import Sum, OuterRef, Subquery, IntegerField, BooleanField, CharField, Value, When, Case, F
from django.db.models.functions import Coalesce

from apps.inventory.models import (
    InventorySnapshot,
    InventoryCount,
    InventoryMovement
)
from apps.products.models import ProductBatch


class InventorySelector:

    @staticmethod
    def get_active_session(store_id):
        return InventorySession.objects.filter(
            store_id=store_id,
            status=InventorySession.Status.ACTIVE
        ).first()

    @staticmethod
    def get_session(pk):
        # ✅ YAXSHI: `get_object_or_404` orqali topilmagan session uchun aniq 404 qaytariladi.
        return get_object_or_404(InventorySession, pk=pk)



    @staticmethod
    def get_inventory_list(session_id, statuses=None):

        counts_subquery = (
            InventoryCount.objects
            .filter(session_id=session_id, product=OuterRef("product"))
            .values("product")
            .annotate(total=Sum("counted_quantity"))
            .values("total")[:1]
        )

        barcode_subquery = (
            ProductBatch.objects
            .filter(product=OuterRef("product"), store_id=OuterRef("store_id"))
            .values("barcode")[:1]
        )

        is_check_subquery = (
            InventoryCount.objects
            .filter(session_id=session_id, product=OuterRef("product"))
            .values("is_check")[:1]
        )

        status_subquery = (
            InventoryCount.objects
            .filter(session_id=session_id, product=OuterRef("product"))
            .values("status")[:1]
        )

        # 🔹 SOLD OUT
        sold_out_subquery = (
            InventoryMovement.objects
            .filter(session_id=session_id, product=OuterRef("product"), type="sale")
            .values("product")
            .annotate(total=Sum("quantity"))
            .values("total")[:1]
        )

        # 🔹 RETURN
        returned_subquery = (
            InventoryMovement.objects
            .filter(session_id=session_id, product=OuterRef("product"), type="return")
            .values("product")
            .annotate(total=Sum("quantity"))
            .values("total")[:1]
        )

        # 🔹 TRANSFER OUT
        transfer_out_subquery = (
            InventoryMovement.objects
            .filter(session_id=session_id, product=OuterRef("product"), type="transfer_out")
            .values("product")
            .annotate(total=Sum("quantity"))
            .values("total")[:1]
        )

        # 🔹 TRANSFER IN
        transfer_in_subquery = (
            InventoryMovement.objects
            .filter(session_id=session_id, product=OuterRef("product"), type="transfer_in")
            .values("product")
            .annotate(total=Sum("quantity"))
            .values("total")[:1]
        )

        # 🔹 ENTRY
        entry_subquery = (
            InventoryMovement.objects
            .filter(session_id=session_id, product=OuterRef("product"), type="entry")
            .values("product")
            .annotate(total=Sum("quantity"))
            .values("total")[:1]
        )


        qs = (
            InventorySnapshot.objects
            .filter(session_id=session_id)
            .select_related("product")
            .annotate(
                # ⚠️ MUAMMO [PERFORMANCE]: Bitta queryset ichida 8 ta korrelyatsiyalangan Subquery ishlatilgan.
                # Sabab: har snapshot qatori uchun count, barcode va movement turlari alohida subquery bilan hisoblanadi.
                # Natija: mahsulotlar soni katta bo'lsa SQL planner og'irlashadi va endpoint sekinlashadi.
                # ✅ YECHIM:
                # movement_totals = InventoryMovement.objects.filter(session_id=session_id).values("product_id", "type").annotate(total=Sum("quantity"))
                # counts = InventoryCount.objects.filter(session_id=session_id).values("product_id", "counted_quantity", "is_check", "status")
                # snapshotlarni product_id bo'yicha Python map bilan birlashtirish yoki materialized aggregate jadval ishlatish.
                # PERFORMANCE: har bir snapshot qatori uchun bir nechta `Subquery` — katta inventarizatsiyada
                # SQL rejasi og'irlashishi mumkin; ba'zi hollarda materialized view / yig'ilgan jadval
                # yoki kamroq qadamli annotate strategiyasi ko'rib chiqiladi.
                counted=Coalesce(Subquery(counts_subquery, output_field=IntegerField()), 0),
                barcode=Subquery(barcode_subquery),

                sold_out=Coalesce(Subquery(sold_out_subquery, output_field=IntegerField()), 0),
                returned=Coalesce(Subquery(returned_subquery, output_field=IntegerField()), 0),
                transfer_out=Coalesce(Subquery(transfer_out_subquery, output_field=IntegerField()), 0),
                transfer_in=Coalesce(Subquery(transfer_in_subquery, output_field=IntegerField()), 0),
                entry=Coalesce(Subquery(entry_subquery, output_field=IntegerField()), 0),

                is_check=Coalesce(Subquery(is_check_subquery, output_field=BooleanField()), False),
                status=Coalesce(Subquery(status_subquery, output_field=CharField()), Value("p")),
            )
        )


        # 🔥 STATUS FILTER
        if statuses:
            qs = qs.filter(status__in=statuses)

        return qs


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [get_inventory_list dagi ko'p Subquery strategiyasini aggregate map yoki materialized hisobga almashtirish]
# ═══════════════════════════════
