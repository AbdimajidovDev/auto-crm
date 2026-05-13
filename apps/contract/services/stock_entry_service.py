from decimal import Decimal

from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from apps.inventory.services.inventory_hooks_service import handle_stock_entry
from apps.products.models import ProductBatch
from apps.contract.models import StockEntry, StockEntryItem, SupplierTransaction
from apps.products.utils.barcode_utility import generate_unique_barcode, generate_barcode_image


class StockEntryService:

    @staticmethod
    @transaction.atomic
    def create_entry(*, supplier, store, items, paid_amount, user):
        # ✅ YAXSHI: Kirim yaratish, batch update, itemlar va transaction bitta `transaction.atomic` ichida.
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
            # ⚠️ MUAMMO [PERFORMANCE]: Har item uchun ProductBatch lookup va update/create loop ichida bajariladi.
            # Sabab: batchlar product_id bo'yicha oldindan select_for_update bilan xaritalanmagan.
            # Natija: ko'p itemli kirimda transaction uzoq lock ushlab turadi va query soni itemlar soniga bog'liq bo'ladi.
            # ✅ YECHIM:
            # product_ids = [item["product"].id for item in items]
            # batches = ProductBatch.objects.select_for_update().filter(store=store, product_id__in=product_ids)
            # ProductBatch.objects.bulk_update(updated_batches, ["quantity", "purchase_price", "selling_price"])
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
                # ⚠️ MUAMMO [PERFORMANCE]: Barcode image generation transaction ichida bajarilmoqda.
                # Sabab: fayl/rasm generatsiyasi DB lock ushlab turgan paytda CPU/I/O ishlatadi.
                # Natija: kirim transactioni uzayadi, parallel requestlar kutib qolishi mumkin.
                # ✅ YECHIM:
                # barcode yaratishni qoldirib, shtrix_code image generationni transaction.on_commit yoki background taskga chiqarish.
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

        # eng oxirida
        handle_stock_entry(entry)

        return entry


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 2
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [ProductBatch loop update strategiyasini bulk update/createga o'tkazish]
# ═══════════════════════════════
