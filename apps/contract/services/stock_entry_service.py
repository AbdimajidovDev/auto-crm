from django.db import transaction
from django.db.models import F
from apps.inventory.services.inventory_hooks_service import handle_stock_entry
from apps.products.models import ProductBatch
from apps.contract.models import StockEntry, StockEntryItem, SupplierTransaction


class StockEntryService:
    @staticmethod
    @transaction.atomic
    def create_entry(*, supplier, store, items, cash_amount, card_amount, user):
        total_entry_amount = sum(
            item["purchase_price"] * item["quantity"] for item in items
        )

        # Entry yaratish — paid_amount, payment_type, debt_amount
        # StockEntry.save() ichida avtomatik hisoblanadi
        entry = StockEntry.objects.create(
            supplier=supplier,
            store=store,
            total_amount=total_entry_amount,
            cash_amount=cash_amount,
            card_amount=card_amount,
            created_by=user
        )

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

            if product.id in existing_batches:
                batch = existing_batches[product.id]
                batch.quantity = F("quantity") + qty
                batch.purchase_price = p_price
                batch.selling_price = s_price
                batch.wholesale_price = w_price
                batches_to_update.append(batch)
            else:
                batches_to_create.append(
                    ProductBatch(
                        product=product,
                        store=store,
                        quantity=qty,
                        purchase_price=p_price,
                        selling_price=s_price,
                        wholesale_price=w_price,
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
