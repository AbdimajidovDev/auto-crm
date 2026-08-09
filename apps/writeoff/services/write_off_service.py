from decimal import Decimal

from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from apps.common.quantity import validate_quantity_step
from apps.products.models import ProductBatch
from apps.writeoff.models import WriteOff, WriteOffItem


class WriteOffService:
    """
    Spisaniye (write-off) biznes logikasi.

    Asosiy qoida: hisobdan chiqarilgan mahsulot QOLDIQDAN KAMAYADI
    (ProductBatch.quantity), xuddi sotuv/transfer kabi. Barcha o'zgarishlar
    bitta `transaction.atomic` ichida — yarim yozuv qolib ketmaydi.
    """

    # =========================================================================
    # CREATE
    # =========================================================================
    @staticmethod
    @transaction.atomic
    def create_write_off(*, store, items, reason, comment="", user, inventory_session=None):
        """
        items: [{"product": <Product>, "quantity": int}, ...]

        Har bir mahsulot uchun shu do'kondagi ProductBatch topiladi, qoldiq
        yetarliligi tekshiriladi va miqdor kamaytiriladi. Narxlar batchdan
        olinib WriteOffItem ga tarix sifatida yoziladi.
        """
        if not items:
            raise ValidationError("Hech bo'lmaganda bitta mahsulot kerak.")

        # 1. Mahsulot satrlarini birlashtiramiz (bitta mahsulot ikki marta kelsa — yig'amiz)
        # Qadam: juft mahsulotda (is_pair) 0.5, oddiyda faqat butun son
        merged = {}
        for it in items:
            product = it["product"]
            qty = validate_quantity_step(
                it["quantity"], is_pair=product.is_pair, product_name=product.name
            )
            merged[product.id] = merged.get(product.id, Decimal("0")) + qty

        product_ids = list(merged.keys())

        # 2. Kerakli batchlarni BITTA query bilan olamiz + lock (race condition oldini olish)
        batches = {
            b.product_id: b
            for b in ProductBatch.objects.select_for_update().filter(
                store=store,
                product_id__in=product_ids,
            )
        }

        # 3. Validatsiya + tayyorlash
        product_map = {it["product"].id: it["product"] for it in items}
        total_amount = Decimal("0")
        batches_to_update = []
        item_objs = []

        for product_id, qty in merged.items():
            product = product_map[product_id]
            batch = batches.get(product_id)

            if batch is None:
                raise ValidationError(f"{product.name}: bu do'konda mahsulot qoldig'i yo'q.")

            if batch.quantity < qty:
                raise ValidationError(
                    f"{product.name}: qoldiq yetarli emas (mavjud: {batch.quantity}, kerak: {qty})."
                )

            batch.quantity = F("quantity") - qty
            batches_to_update.append(batch)

            total_amount += batch.purchase_price * qty
            item_objs.append(
                WriteOffItem(
                    product=product,
                    quantity=qty,
                    purchase_price=batch.purchase_price,
                    selling_price=batch.selling_price,
                )
            )

        # 4. WriteOff (header) yaratamiz
        write_off = WriteOff.objects.create(
            store=store,
            reason=reason,
            comment=comment or "",
            total_amount=total_amount,
            created_by=user,
            inventory_session=inventory_session,
        )

        for obj in item_objs:
            obj.write_off = write_off

        # 5. Stockni kamaytiramiz va itemlarni yozamiz (bulk)
        ProductBatch.objects.bulk_update(batches_to_update, ["quantity"])
        WriteOffItem.objects.bulk_create(item_objs)

        # 6. Low-stock baholash (sotuv kabi — qoldiq kamaydi)
        from apps.inventory.services import LowStockService
        LowStockService.schedule_evaluation(store=store, product_ids=product_ids)

        return write_off

    # =========================================================================
    # INVENTARIZATSIYA KAMOMADI (record-only — stockka TEGMAYDI)
    # =========================================================================
    @staticmethod
    @transaction.atomic
    def record_inventory_shortage(*, session, shortages, user=None):
        """
        Inventarizatsiyada kam chiqqan (topilmagan) tovarlar uchun avtomatik
        spisaniye AUDIT yozuvini yaratadi.

        ⚠️ Bu metod ProductBatch.quantity ga TEGMAYDI — chunki qoldiq
        `InventoryService.finalize()` ichida allaqachon to'g'rilangan. Bu yerda
        faqat "nima uchun qoldiq kamaydi" degan tarix/audit yoziladi (ikki marta
        kamaytirib yuborilmasligi uchun).

        shortages: [{"product_id", "quantity", "purchase_price", "selling_price"}]
        """
        shortages = [s for s in shortages if s["quantity"] > 0]
        if not shortages:
            return None

        total_amount = sum(
            (s["purchase_price"] * s["quantity"] for s in shortages),
            Decimal("0"),
        )

        write_off = WriteOff.objects.create(
            store=session.store,
            reason=WriteOff.Reason.INVENTORY,
            comment=f"Inventarizatsiya #{session.id} kamomadi (avtomatik)",
            total_amount=total_amount,
            created_by=user,
            inventory_session=session,
        )

        WriteOffItem.objects.bulk_create([
            WriteOffItem(
                write_off=write_off,
                product_id=s["product_id"],
                quantity=s["quantity"],
                purchase_price=s["purchase_price"],
                selling_price=s["selling_price"],
            )
            for s in shortages
        ])

        return write_off

    # =========================================================================
    # DELETE (stockni qaytaradi — xato yozuvni bekor qilish)
    # =========================================================================
    @staticmethod
    @transaction.atomic
    def delete_write_off(*, write_off):
        """
        Spisaniyeni o'chiradi va hisobdan chiqarilgan miqdorni QOLDIQQA QAYTARADI.
        (Xato kiritilgan spisaniyeni bekor qilish uchun.)
        """
        locked = WriteOff.objects.select_for_update().get(pk=write_off.pk)
        items = list(locked.items.all())

        product_ids = [i.product_id for i in items]
        batches = {
            b.product_id: b
            for b in ProductBatch.objects.select_for_update().filter(
                store=locked.store_id,
                product_id__in=product_ids,
            )
        }

        batches_to_update = []
        for i in items:
            batch = batches.get(i.product_id)
            if batch is None:
                # Batch o'chib ketgan bo'lsa — qayta yaratamiz
                ProductBatch.objects.create(
                    store_id=locked.store_id,
                    product_id=i.product_id,
                    quantity=i.quantity,
                    purchase_price=i.purchase_price,
                    selling_price=i.selling_price,
                )
                continue
            batch.quantity = F("quantity") + i.quantity
            batches_to_update.append(batch)

        if batches_to_update:
            ProductBatch.objects.bulk_update(batches_to_update, ["quantity"])

        store = locked.store
        locked.delete()

        from apps.inventory.services import LowStockService
        LowStockService.schedule_evaluation(store=store, product_ids=product_ids)

    # =========================================================================
    # UPDATE (faqat metama'lumot: sabab/izoh — stockka tegmaydi)
    # =========================================================================
    @staticmethod
    @transaction.atomic
    def update_write_off(*, write_off, reason=None, comment=None):
        """
        Faqat `reason` va `comment` ni yangilaydi. Mahsulot satrlarini
        o'zgartirish qo'llab-quvvatlanmaydi (stock nomuvofiqligini oldini olish
        uchun — o'rniga o'chirib qayta yarating).
        """
        fields = []
        if reason is not None:
            write_off.reason = reason
            fields.append("reason")
        if comment is not None:
            write_off.comment = comment
            fields.append("comment")

        if fields:
            write_off.save(update_fields=fields)
        return write_off


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 10 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [—]
# Izoh: Bu write-path (GET emas), lekin query-hygiene namunaviy:
#   - Batchlar loop DAN TASHQARIDA bitta filter(product_id__in=...) + select_for_update bilan
#     olinadi (loop ichida query yo'q).
#   - Stock o'zgarishlari F() ifodalari bilan bulk_update, itemlar bulk_create.
#   - Low-stock baholash transaction.on_commit orqali rejalashtiriladi (asosiy tranzaksiyani
#     bloklamaydi). N+1 va loop-ichi-query muammolari yo'q.
# ═══════════════════════════════
