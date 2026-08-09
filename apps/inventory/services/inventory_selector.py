from django.shortcuts import get_object_or_404

from django.db.models import DecimalField, Sum, Q, Exists, OuterRef, F
from django.db.models.functions import Coalesce

from apps.inventory.models import (
    InventorySession,
    InventorySnapshot,
    InventoryCount,
    InventoryMovement,
)


# ═══════════════════════════════════════════════════════════════════════════
# 🔬 TAHLIL: eski `get_inventory_list` dagi 8 ta korrelyatsiyalangan Subquery
# ───────────────────────────────────────────────────────────────────────────
# Eski holatda har bir InventorySnapshot qatoriga 8 ta Subquery annotate qilinardi.
# Ular nima uchun ishlatilgan va nima bilan almashtirildi:
#
#   1) counted   ← InventoryCount.Sum(counted_quantity)   [session, product bo'yicha]
#   2) is_check  ← InventoryCount.is_check
#   3) status    ← InventoryCount.status
#        → Bu 3 tasi AYNI BITTA InventoryCount qatoridan keladi (unique (session, product)).
#          3 ta alohida korrelyatsiyalangan subquery O'RNIGA — BITTA query bilan
#          `product_id -> {counted_quantity, is_check, status}` MAP tuzildi (JOIN emas,
#          .values() map). Endi 3 emas, 1 marta o'qiladi va faqat sahifa product'lari uchun.
#
#   4) sold_out       ← InventoryMovement.Sum(quantity) filter type=SALE
#   5) returned       ← InventoryMovement.Sum(quantity) filter type=RETURN
#   6) transfer_out   ← InventoryMovement.Sum(quantity) filter type=TRANSFER_OUT
#   7) transfer_in    ← InventoryMovement.Sum(quantity) filter type=TRANSFER_IN
#   8) entry          ← InventoryMovement.Sum(quantity) filter type=ENTRY
#        → Bu 5 tasi AYNI BITTA jadval (InventoryMovement), ayni (session, product)
#          korrelyatsiya, faqat `type` bilan farq qiladi. 5 ta korrelyatsiyalangan
#          subquery O'RNIGA — BITTA CONDITIONAL AGGREGATION (GROUP BY product_id +
#          `Sum(..., filter=Q(type=...))`) ishlatildi. Bu 1 ta agregat query = 5 qiymat.
#
# ❓ Nega `FilteredRelation` emas?
#    FilteredRelation JOIN uchun ORM RELATSIYA yo'li kerak (masalan snapshot.movements).
#    InventorySnapshot → InventoryMovement o'rtasida FK/reverse relation YO'Q (ikkalasi ham
#    faqat session+product ga ega). Shu sabab bu yerda to'g'ri vosita — conditional
#    aggregation (Sum+filter) bo'lgan alohida GROUP BY query, keyin Python map bilan birlashtirish.
#
# ❓ Nega map (Python birlashtirish) — JOIN emas?
#    PAGINATION bor: biz avval snapshotni sahifalaymiz (20 qator), keyin faqat SHU 20 ta
#    product_id uchun count/movement agregatini olamiz. Ya'ni og'ir agregat butun 12k jadvalга
#    emas, faqat 20 qatorga tushadi. Birlashtirish 20 element ustida — arzon.
#
# 📉 SQL byudjeti (bir sahifa uchun):
#    AVVAL: 1 (COUNT) + 1 (snapshot) + har qatorga 8 subquery ≈ 1+1+(20×8)=162 ifoda
#    HOZIR: 1 (COUNT) + 1 (snapshot page) + 1 (InventoryCount map) + 1 (Movement agg) = 4 query
#           (+ checked ro'yxati 1, checked_count 1 — request boshiga, qatorga bog'liq emas)
#
# 🗂  Indeks: InventoryMovement (session, product) va InventoryCount unique (session, product)
#    allaqachon bor — `product_id__in` va Exists shular orqali tez ishlaydi.
#    Ixtiyoriy yaxshilash: InventoryMovement (session, product, type) — conditional agg FILTER'ini
#    yanada tezlashtiradi (katta sessiyalarda).
# ═══════════════════════════════════════════════════════════════════════════


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

    # ─────────────────────────────────────────────────────────────────────
    # 1) PAGINATION uchun YENGIL base queryset (og'ir subquerylarsiz)
    # ─────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_snapshot_page_qs(session_id, check_status="all"):
        """
        Sahifalash uchun asosiy YENGIL queryset: snapshot + product (select_related).
        Bu yerda OG'IR movement subquerylari YO'Q — ular faqat joriy sahifa uchun
        `enrich_page()` da hisoblanadi.

        check_status: "checked" | "unchecked" | "all"
          is_check InventoryCount'da yotadi (snapshotда emas), shu sabab filtr `Exists`
          (semi-join) bilan qilinadi — (session, product) indeks bo'yicha birinchi
          moslikda to'xtaydi, ya'ni arzon va SQL darajasida (Python filtersiz).
        """
        qs = (
            InventorySnapshot.objects
            .filter(session_id=session_id)
            .select_related("product")
            .order_by("product__name")   # pagination barqaror bo'lishi uchun determinstik tartib
        )

        if check_status in ("checked", "unchecked"):
            is_checked = Exists(
                InventoryCount.objects.filter(
                    session_id=session_id,
                    product_id=OuterRef("product_id"),
                    is_check=True,
                )
            )
            # checked  → is_check=True topilganlar
            # unchecked→ is_check=True topilmaganlar (umuman count yo'q YOKI is_check=False)
            qs = qs.filter(is_checked) if check_status == "checked" else qs.filter(~is_checked)

        return qs

    # ─────────────────────────────────────────────────────────────────────
    # 2) Faqat JORIY SAHIFA qatorlarini boyitish (2 ta agregat query)
    # ─────────────────────────────────────────────────────────────────────
    @staticmethod
    def enrich_page(session_id, snapshot_rows):
        """
        Sahifadagi snapshot obyektlariga count/movement qiymatlarini ATRIBUT sifatida
        o'rnatadi (serializer shu atributlarni o'qiydi: counted, sold_out, ... , is_check, status).

        8 ta korrelyatsiyalangan subquery O'RNIGA:
          - 1 ta InventoryCount query  → counted_quantity, is_check, status (map)
          - 1 ta InventoryMovement CONDITIONAL AGGREGATION (GROUP BY product_id, Sum+filter)
        Ikkalasi ham faqat sahifadagi `product_id` lar bo'yicha (butun jadval emas).
        """
        rows = list(snapshot_rows)
        product_ids = [r.product_id for r in rows]
        if not product_ids:
            return rows

        # 1 query: InventoryCount (session, product unique → productga bittadan qator)
        count_map = {
            c["product_id"]: c
            for c in InventoryCount.objects
            .filter(session_id=session_id, product_id__in=product_ids)
            .values("product_id", "counted_quantity", "is_check", "status")
        }

        # 1 query: InventoryMovement conditional aggregation (5 subquery o'rniga bitta GROUP BY)
        # output_field majburiy: quantity endi Decimal (0.5 qadam), 0 esa butun son —
        # aralash tiplarda Django FieldError beradi
        qty_field = DecimalField(max_digits=12, decimal_places=2)
        T = InventoryMovement.Type
        move_map = {
            m["product_id"]: m
            for m in InventoryMovement.objects
            .filter(session_id=session_id, product_id__in=product_ids)
            .values("product_id")
            .annotate(
                sold_out=Coalesce(Sum("quantity", filter=Q(type=T.SALE)), 0, output_field=qty_field),
                returned=Coalesce(Sum("quantity", filter=Q(type=T.RETURN)), 0, output_field=qty_field),
                transfer_out=Coalesce(Sum("quantity", filter=Q(type=T.TRANSFER_OUT)), 0, output_field=qty_field),
                transfer_in=Coalesce(Sum("quantity", filter=Q(type=T.TRANSFER_IN)), 0, output_field=qty_field),
                entry=Coalesce(Sum("quantity", filter=Q(type=T.ENTRY)), 0, output_field=qty_field),
            )
        }

        for r in rows:
            c = count_map.get(r.product_id) or {}
            m = move_map.get(r.product_id) or {}
            r.counted = c.get("counted_quantity") or 0
            r.is_check = c.get("is_check") or False
            r.status = c.get("status") or "p"
            r.barcode = r.product.barcode
            r.sold_out = m.get("sold_out") or 0
            r.returned = m.get("returned") or 0
            r.transfer_out = m.get("transfer_out") or 0
            r.transfer_in = m.get("transfer_in") or 0
            r.entry = m.get("entry") or 0

        return rows

    # ─────────────────────────────────────────────────────────────────────
    # 3) checked ro'yxati — YENGIL, ALOHIDA query (asosiy 8-subquery ISHLATILMAYDI)
    # ─────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_checked(session_id):
        """
        Butun sessiya bo'yicha belgilangan (is_check=True) mahsulotlar — YENGIL.
        Asosiy product query (movement subquerylari) ISHLATILMAYDI: faqat
        InventoryCount + product JOIN, bitta query. Pagination'dan mustaqil.
        """
        return list(
            InventoryCount.objects
            .filter(session_id=session_id, is_check=True)
            .order_by("product__name")
            .values(
                "product_id",
                "status",
                "is_check",
                product_name=F("product__name"),
                barcode=F("product__barcode"),
                scanned=F("counted_quantity"),
            )
        )

    # ─────────────────────────────────────────────────────────────────────
    # 4) checked_count — SQL COUNT (Python'da list aylantirilmaydi)
    # ─────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_checked_count(session_id):
        """SELECT COUNT(*) ... WHERE session=? AND is_check=true — bitta arzon so'rov."""
        return (
            InventoryCount.objects
            .filter(session_id=session_id, is_check=True)
            .count()
        )


# ═══════════════════════════════
# 📊 FAYL XULOSASI (yangilangan)
# Kritik muammolar soni: 0
# Performance muammolari: 0  (avval 1 — 8 korrelyatsiyalangan subquery aggregate+Exists+map bilan almashtirildi)
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10  (avval 7/10)
# ✅ BAJARILDI: get_inventory_list (monolit, 8 subquery) → get_snapshot_page_qs (yengil, paginate-able) +
#    enrich_page (2 agregat query, faqat sahifa) + get_checked / get_checked_count (yengil, alohida).
# Prioritet bo'yicha birinchi hal qilinishi kerak: [ixtiyoriy — InventoryMovement (session, product, type) indeksi]
# ═══════════════════════════════
