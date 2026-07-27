"""
stock_entry_return_service.py — xarid (kirim) bo'yicha ta'minotchiga mahsulot QAYTARISH.

Hisob-kitob qoidalari (hammasi bitta tranzaksiyada):
  1. Har bir satr uchun qaytarish limiti: olingan miqdor − oldin qaytarilganlar.
  2. Ombor: tanlangan do'kondagi ProductBatch miqdori kamayadi — omborda
     yetarli qoldiq bo'lmasa (sotilgan bo'lsa) qaytarib bo'lmaydi.
  3. Pul: qaytim summasi avval shu kirimning QOLDIQ QARZIDAN kamaytiriladi
     (SupplierTransaction type='ret'), ortgan qismi refund_amount sifatida
     yoziladi — ta'minotchi pul qaytarishi kerak bo'lgan summa.
  4. Kirimning asl yozuvlari (items, to'lovlar, total_amount) o'zgartirilmaydi.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum

from apps.products.models import ProductBatch
from apps.contract.models import (
    StockEntry,
    StockEntryReturn,
    StockEntryReturnItem,
    SupplierTransaction,
)
from apps.contract.services.supplier_service import SupplierPaymentService


class StockEntryReturnService:

    @staticmethod
    @transaction.atomic
    def create_return(*, entry, items, user, note="") -> StockEntryReturn:
        """
        items: [{"entry_item": StockEntryItem, "quantity": int}, ...]

        Qaytaradi: yaratilgan StockEntryReturn (items prefetch qilinmagan).
        Xato satr bo'lsa ValidationError — hech narsa yozilmaydi.
        """
        if not items:
            raise ValidationError("Qaytariladigan mahsulotlar tanlanmagan.")

        # Parallel qaytarish/to'lov poygasini oldini olish uchun entry qulflanadi
        entry = StockEntry.objects.select_for_update().get(pk=entry.pk)

        # Bitta so'rovda bir satr ikki marta kelmasin
        seen_item_ids = set()
        for it in items:
            if it["entry_item"].id in seen_item_ids:
                raise ValidationError(
                    f"Satr takrorlangan: {it['entry_item'].product.name}"
                )
            seen_item_ids.add(it["entry_item"].id)

        # Oldin qaytarilgan miqdorlar (satr kesimida)
        returned_map = {
            row["entry_item"]: row["total"]
            for row in StockEntryReturnItem.objects
            .filter(entry_item__entry=entry)
            .values("entry_item")
            .annotate(total=Sum("quantity"))
        }

        # Ombor qoldiqlari — qulf bilan (bir vaqtdagi sotuv/qaytarish poygasi uchun)
        product_ids = [it["entry_item"].product_id for it in items]
        batches = {
            b.product_id: b
            for b in ProductBatch.objects.select_for_update().filter(
                store=entry.store, product_id__in=product_ids
            )
        }

        # ── Validatsiya ───────────────────────────────────────────────────────
        total_amount = Decimal("0")
        for it in items:
            entry_item = it["entry_item"]
            qty = it["quantity"]

            if entry_item.entry_id != entry.id:
                raise ValidationError(
                    f"Satr bu xaridga tegishli emas: {entry_item.product.name}"
                )
            if qty <= 0:
                raise ValidationError(
                    f"Miqdor 0 dan katta bo'lishi kerak: {entry_item.product.name}"
                )

            already = returned_map.get(entry_item.id, 0)
            remaining = entry_item.quantity - already
            if qty > remaining:
                raise ValidationError(
                    f"{entry_item.product.name}: qaytarish mumkin bo'lgan miqdor {remaining} dona "
                    f"(olingan {entry_item.quantity}, qaytarilgan {already})."
                )

            batch = batches.get(entry_item.product_id)
            available = batch.quantity if batch else 0
            if qty > available:
                raise ValidationError(
                    f"{entry_item.product.name}: omborda yetarli qoldiq yo'q "
                    f"(qoldiq {available} dona — sotilgan bo'lishi mumkin)."
                )

            total_amount += entry_item.purchase_price * qty

        # ── Pul hisob-kitobi: avval qarzdan, ortgani refund ───────────────────
        remaining_debt = SupplierPaymentService.get_remaining_debt(entry)
        if remaining_debt < 0:
            remaining_debt = Decimal("0")
        debt_cancelled = min(total_amount, remaining_debt)
        refund_amount = total_amount - debt_cancelled

        # ── Yozish ────────────────────────────────────────────────────────────
        stock_return = StockEntryReturn.objects.create(
            entry=entry,
            total_amount=total_amount,
            debt_cancelled=debt_cancelled,
            refund_amount=refund_amount,
            note=(note or "").strip(),
            created_by=user,
        )

        StockEntryReturnItem.objects.bulk_create([
            StockEntryReturnItem(
                stock_return=stock_return,
                entry_item=it["entry_item"],
                product_id=it["entry_item"].product_id,
                quantity=it["quantity"],
                purchase_price=it["entry_item"].purchase_price,
                amount=it["entry_item"].purchase_price * it["quantity"],
            )
            for it in items
        ])

        # Ombordan chiqarish — F() bilan atomar kamaytirish
        for it in items:
            batch = batches[it["entry_item"].product_id]
            batch.quantity = F("quantity") - it["quantity"]
        ProductBatch.objects.bulk_update(
            list(batches.values()), ["quantity"]
        )

        # Qarzdan kamaytirish — alohida 'ret' turi ('pay' emas: to'lov/chiqim
        # hisobotlariga aralashmaydi, get_total_debt/get_remaining_debt uni ayiradi)
        if debt_cancelled > 0:
            SupplierTransaction.objects.create(
                supplier=entry.supplier,
                entry=entry,
                amount=debt_cancelled,
                type=SupplierTransaction.TransactionType.RETURN,
                note=f"Qaytim #{stock_return.pk}: mahsulot qaytarildi, qarz kamaydi",
            )

        return stock_return
