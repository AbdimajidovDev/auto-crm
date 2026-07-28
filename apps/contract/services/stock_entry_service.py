from decimal import Decimal

from django.db import transaction
from django.db.models import F
from apps.inventory.services.inventory_hooks_service import handle_stock_entry
from apps.products.models import ProductBatch
from apps.contract.models import StockEntry, StockEntryItem, StockEntryPayment, SupplierTransaction


def _normalize_payments(payments, cash_amount, card_amount, bank_card):
    """
    Har doim split to'lovlar ro'yxatini qaytaradi:
      [{"type": "cash"|"card", "amount": Decimal, "bank_card": BankCard|None}, ...]
    payments berilmagan bo'lsa (Excel import, eski klientlar) yassi
    cash_amount/card_amount/bank_card maydonlaridan sintez qilinadi.
    """
    if payments:
        return [
            {
                "type": p["type"],
                "amount": Decimal(str(p["amount"])),
                "bank_card": p.get("bank_card"),
            }
            for p in payments
            if Decimal(str(p["amount"])) > 0
        ]

    synthesized = []
    if cash_amount and cash_amount > 0:
        synthesized.append({"type": StockEntryPayment.Type.CASH, "amount": cash_amount, "bank_card": None})
    if card_amount and card_amount > 0:
        synthesized.append({"type": StockEntryPayment.Type.CARD, "amount": card_amount, "bank_card": bank_card})
    return synthesized


class StockEntryService:
    @staticmethod
    @transaction.atomic
    def create_entry(*, supplier, store, items, user, payments=None,
                     cash_amount=0, card_amount=0, bank_card=None, note=""):
        total_entry_amount = sum(
            item["purchase_price"] * item["quantity"] for item in items
        )

        payment_rows = _normalize_payments(payments, cash_amount, card_amount, bank_card)
        zero = Decimal("0")
        total_cash = sum((p["amount"] for p in payment_rows if p["type"] == StockEntryPayment.Type.CASH), zero)
        total_card = sum((p["amount"] for p in payment_rows if p["type"] == StockEntryPayment.Type.CARD), zero)
        card_rows = [p for p in payment_rows if p["type"] == StockEntryPayment.Type.CARD]

        # Eski bank_card FK maydoni faqat bitta karta bo'lganda ma'noli
        # (bir nechta karta bo'lsa haqiqiy taqsimot entry.payments qatorlarida)
        legacy_bank_card = card_rows[0]["bank_card"] if len(card_rows) == 1 else None

        # Entry yaratish — paid_amount, payment_type, debt_amount
        # StockEntry.save() ichida avtomatik hisoblanadi
        entry = StockEntry.objects.create(
            supplier=supplier,
            store=store,
            total_amount=total_entry_amount,
            cash_amount=total_cash,
            card_amount=total_card,
            bank_card=legacy_bank_card,
            note=(note or "").strip(),
            created_by=user
        )

        # Split to'lov qatorlari (sotuvdagi Payment bilan bir xil naqsh)
        payment_objs = [
            StockEntryPayment(
                entry=entry,
                amount=p["amount"],
                type=p["type"],
                bank_card=p.get("bank_card"),
            )
            for p in payment_rows
        ]
        # bulk_create save() ni chaqirmaydi — invariantni qo'lda tekshiramiz
        for payment in payment_objs:
            payment.clean()
        StockEntryPayment.objects.bulk_create(payment_objs)

        product_ids = [item["product"].id for item in items]

        existing_batches = {
            batch.product_id: batch
            for batch in ProductBatch.objects.select_for_update().filter(
                store=store,
                product_id__in=product_ids
            )
        }

        batches_to_update = []
        batches_to_create = []
        item_objs = []

        # Bitta kirimda bir mahsulot bir necha qatorda kelishi mumkin (10 dona +
        # 15 dona). Qatorlar o'z narxlari bilan alohida saqlanadi, LEKIN ombor
        # qoldig'i mahsulot bo'yicha JAMLANADI.
        # Ilgari har qator `batch.quantity = F("quantity") + qty` ni qayta
        # yozardi — natijada faqat oxirgi qator qo'shilib, qolgani yo'qolardi;
        # yangi mahsulotda esa bir xil (store, product) uchun ikkita
        # ProductBatch yaratilib, unique constraint 500 bilan yiqilardi.
        merged = {}
        for item in items:
            product = item["product"]
            qty = item["quantity"]
            p_price = item["purchase_price"]
            s_price = item["selling_price"]
            w_price = item["wholesale_price"]

            item_objs.append(
                StockEntryItem(
                    entry=entry,
                    product=product,
                    quantity=qty,
                    purchase_price=p_price,
                    selling_price=s_price,
                    wholesale_price=w_price,
                )
            )

            if product.id in merged:
                merged[product.id]["quantity"] += qty
                # Narxlar oxirgi qator bo'yicha yangilanadi (avvalgi xatti-harakat)
                merged[product.id].update(
                    purchase_price=p_price, selling_price=s_price, wholesale_price=w_price
                )
            else:
                merged[product.id] = {
                    "product": product,
                    "quantity": qty,
                    "purchase_price": p_price,
                    "selling_price": s_price,
                    "wholesale_price": w_price,
                }

        for product_id, data in merged.items():
            if product_id in existing_batches:
                batch = existing_batches[product_id]
                batch.quantity = F("quantity") + data["quantity"]
                batch.purchase_price = data["purchase_price"]
                batch.selling_price = data["selling_price"]
                batch.wholesale_price = data["wholesale_price"]
                batches_to_update.append(batch)
            else:
                batches_to_create.append(
                    ProductBatch(
                        product=data["product"],
                        store=store,
                        quantity=data["quantity"],
                        purchase_price=data["purchase_price"],
                        selling_price=data["selling_price"],
                        wholesale_price=data["wholesale_price"],
                    )
                )

        if batches_to_update:
            ProductBatch.objects.bulk_update(
                batches_to_update,
                ["quantity", "purchase_price", "selling_price", "wholesale_price"]
            )
        if batches_to_create:
            ProductBatch.objects.bulk_create(batches_to_create)

        StockEntryItem.objects.bulk_create(item_objs)

        # Qarzdorlik — debt_amount endi entry.save() ichida hisoblangan
        if entry.debt_amount > 0:
            SupplierTransaction.objects.create(
                supplier=supplier,
                entry=entry,
                amount=entry.debt_amount,
                type=SupplierTransaction.TransactionType.INVENTORY_IN,
                note=f"Entry #{entry.pk} orqali qarzga mahsulot olindi"
            )

        handle_stock_entry(entry)
        return entry


# from decimal import Decimal
#
# from django.db import transaction
# from django.db.models import F
# from rest_framework.exceptions import ValidationError
#
# from apps.inventory.services.inventory_hooks_service import handle_stock_entry
# from apps.products.models import ProductBatch
# from apps.contract.models import StockEntry, StockEntryItem, SupplierTransaction

# class StockEntryService:
#
#     @staticmethod
#     @transaction.atomic
#     def create_entry(*, supplier, store, items, paid_amount, payment_type, user):
#
#         # 1. Validation: paid_amount ni oldindan hisoblash
#         total_entry_amount = sum(
#             item["purchase_price"] * item["quantity"] for item in items
#         )
#
#         if total_entry_amount < paid_amount:
#             raise ValidationError("To'lov umumiy narxdan oshib ketdi!")
#
#         # 2. Entry yaratish
#         entry = StockEntry.objects.create(
#             supplier=supplier,
#             store=store,
#             total_amount=total_entry_amount,
#             paid_amount=paid_amount,
#             payment_type=payment_type,
#             created_by=user
#         )
#
#         # 3. Barcha product_id larni oldindan yig'ish
#         product_ids = [item["product"].id for item in items]
#
#         # 4. Mavjud batchlarni BITTA query bilan olish + lock
#         existing_batches = {
#             batch.product_id: batch
#             for batch in ProductBatch.objects.select_for_update().filter(
#                 store=store,
#                 product_id__in=product_ids
#             )
#         }
#
#         batches_to_update = []
#         batches_to_create = []
#         item_objs = []
#
#         for item in items:
#             product = item["product"]
#             qty = item["quantity"]
#             p_price = item["purchase_price"]
#             s_price = item["selling_price"]
#
#             item_objs.append(
#                 StockEntryItem(
#                     entry=entry,
#                     product=product,
#                     quantity=qty,
#                     purchase_price=p_price,
#                     selling_price=s_price
#                 )
#             )
#
#             if product.id in existing_batches:
#                 batch = existing_batches[product.id]
#                 batch.quantity = F("quantity") + qty
#                 batch.purchase_price = p_price
#                 batch.selling_price = s_price
#                 batches_to_update.append(batch)
#             else:
#                 batches_to_create.append(
#                     ProductBatch(
#                         product=product,
#                         store=store,
#                         quantity=qty,
#                         purchase_price=p_price,
#                         selling_price=s_price
#                     )
#                 )
#
#         # 5. Bulk operatsiyalar — minimal query
#         if batches_to_update:
#             ProductBatch.objects.bulk_update(
#                 batches_to_update,
#                 ["quantity", "purchase_price", "selling_price"]
#             )
#
#         if batches_to_create:
#             ProductBatch.objects.bulk_create(batches_to_create)
#
#         StockEntryItem.objects.bulk_create(item_objs)
#
#         # 6. Qarzdorlik logikasi
#         debt_amount = total_entry_amount - paid_amount
#         if debt_amount > 0:
#             SupplierTransaction.objects.create(
#                 supplier=supplier,
#                 entry=entry,
#                 amount=debt_amount,
#                 type=SupplierTransaction.TransactionType.INVENTORY_IN,
#                 note=f"Entry #{entry.pk} orqali qarzga mahsulot olindi"
#             )
#
#         handle_stock_entry(entry)
#
#         return entry
