from decimal import Decimal

from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from apps.products.models import ProductBatch
from apps.contract.models import StockEntry, StockEntryItem, SupplierTransaction
from apps.products.utils.barcode_utility import generate_unique_barcode, generate_barcode_image


# class StockEntryService:
#
#     @staticmethod
#     @transaction.atomic
#     def create_entry(*, supplier, store, items, paid_amount, user):
#         # 1. Header yaratish
#         entry = StockEntry.objects.create(
#             supplier=supplier,
#             store=store,
#             paid_amount=paid_amount,
#             created_by=user
#         )
#
#         total_entry_amount = Decimal("0")
#         item_objs = []
#
#         # 2. Mahsulotlarni aylanish va Stockni yangilash
#         for item in items:
#             product = item["product"]
#             qty = item["quantity"]
#             p_price = item["purchase_price"]
#             s_price = item["selling_price"]
#
#             line_total = p_price * qty
#             total_entry_amount += line_total
#
#             # ProductBatch yangilash (Race condition oldini olish uchun select_for_update)
#             batch, created = ProductBatch.objects.select_for_update().get_or_create(
#                 store=store,
#                 product=product,
#                 defaults={
#                     'quantity': 0,
#                     'purchase_price': p_price,
#                     'selling_price': s_price,
#                     'barcode': generate_unique_barcode()
#                 }
#             )
#
#             if not created:
#                 batch.quantity = F("quantity") + qty
#                 batch.purchase_price = p_price
#                 batch.selling_price = s_price
#                 batch.save()
#             else:
#                 # Agar yangi yaratilgan bo'lsa, initial qty'ni update qilamiz
#                 batch.quantity = qty
#                 batch.save()
#
#             item_objs.append(
#                 StockEntryItem(
#                     entry=entry, product=product, quantity=qty,
#                     purchase_price=p_price, selling_price=s_price
#                 )
#             )
#
#         # 3. Itemlarni bulk yaratish
#         StockEntryItem.objects.bulk_create(item_objs)
#
#         # 4. Entry'ning umumiy summasini saqlash
#         entry.total_amount = total_entry_amount
#         entry.save()
#
#         # 5. QARZDORLIK LOGIKASI (Taminotchi bilan hisob-kitob)
#         debt_amount = total_entry_amount - paid_amount
#
#         if debt_amount > 0:
#             # Taminotchidan qarzimiz ko'paydi
#             SupplierTransaction.objects.create(
#                 supplier=supplier,
#                 entry=entry,
#                 amount=debt_amount,
#                 type=SupplierTransaction.TransactionType.INVENTORY_IN,
#                 note=f"Entry #{entry.pk} orqali qarzga mahsulot olindi"
#             )
#
#             # Bu yerda Supplier modelida balance degan maydon bo'lsa, uni ham update qilish mumkin
#             # Supplier.objects.filter(id=supplier.id).update(balance=F('balance') + debt_amount)
#
#         return entry


class StockEntryService:

    @staticmethod
    @transaction.atomic
    def create_entry(*, supplier, store, items, paid_amount, user):
        # 1. Header yaratish
        entry = StockEntry.objects.create(
            supplier=supplier,
            store=store,
            paid_amount=paid_amount,
            created_by=user
        )

        total_entry_amount = Decimal("0")
        item_objs = []

        # 2. Mahsulotlarni aylanish
        for item in items:
            product = item["product"]
            qty = item["quantity"]
            p_price = item["purchase_price"]
            s_price = item["selling_price"]

            line_total = p_price * qty
            total_entry_amount += line_total

            # 🔥 Race condition oldini olish uchun select_for_update
            batch = ProductBatch.objects.select_for_update().filter(
                store=store,
                product=product
            ).first()

            if batch:
                # 🔄 Mavjud batchni yangilash
                ProductBatch.objects.filter(id=batch.pk).update(
                    quantity=F("quantity") + qty,
                    purchase_price=p_price,
                    selling_price=s_price
                )
            else:
                # ➕ Yangi batch yaratish (barcode va shtrix_code bilan)
                barcode = generate_unique_barcode()
                ProductBatch.objects.create(
                    product=product,
                    store=store,
                    quantity=qty,
                    purchase_price=p_price,
                    selling_price=s_price,
                    barcode=barcode,
                    # Mana bu yerda rasm generatsiya qilinadi
                    shtrix_code=generate_barcode_image(barcode)
                )

            item_objs.append(
                StockEntryItem(
                    entry=entry,
                    product=product,
                    quantity=qty,
                    purchase_price=p_price,
                    selling_price=s_price
                )
            )

        # 3. Itemlarni bulk yaratish
        StockEntryItem.objects.bulk_create(item_objs)

        # 4. Entry'ning umumiy summasini saqlash
        entry.total_amount = total_entry_amount

        if total_entry_amount < paid_amount:
            raise ValidationError("To'lov umumiy narxdan oshib ketdi!")

        entry.save()

        # 5. QARZDORLIK LOGIKASI
        debt_amount = total_entry_amount - paid_amount
        if debt_amount > 0:
            SupplierTransaction.objects.create(
                supplier=supplier,
                entry=entry,
                amount=debt_amount,
                type=SupplierTransaction.TransactionType.INVENTORY_IN,
                note=f"Entry #{entry.pk} orqali qarzga mahsulot olindi"
            )

        return entry


# class StockEntryService:
#
#     @staticmethod
#     @transaction.atomic
#     def create_entry(*, supplier, store, items, user):
#
#         # 🔹 create header
#         entry = StockEntry.objects.create(
#             supplier=supplier,
#             store=store,
#             created_by=user
#         )
#
#         item_objs = []
#
#         for item in items:
#             product = item["product"]
#             quantity = item["quantity"]
#             purchase_price = item["purchase_price"]
#             selling_price = item["selling_price"]
#
#             from django.db.models import F
#
#             batch = ProductBatch.objects.select_for_update().filter(
#                 store=store,
#                 product=product
#             ).first()
#
#             if batch:
#                 # 🔥 MUHIM: barcode o‘zgarmaydi
#                 ProductBatch.objects.filter(id=batch.pk).update(
#                     quantity=F("quantity") + quantity,
#                     purchase_price=purchase_price,
#                     selling_price=selling_price
#                 )
#             else:
#                 barcode = generate_unique_barcode()
#
#                 # ➕ faqat birinchi marta create
#                 ProductBatch.objects.create(
#                     product=product,
#                     store=store,
#                     quantity=quantity,
#                     purchase_price=purchase_price,
#                     selling_price=selling_price,
#                     barcode=barcode,
#                     shtrix_code=generate_barcode_image(barcode)
#                 )
#
#             item_objs.append(
#                 StockEntryItem(
#                     entry=entry,
#                     product=product,
#                     quantity=quantity,
#                     purchase_price=purchase_price,
#                     selling_price=selling_price
#                 )
#             )
#
#         # BULK INSERT (safe, chunki signal kerak emas)
#         StockEntryItem.objects.bulk_create(item_objs)
#
#         return entry
