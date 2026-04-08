from django.db import transaction
from django.db.models import F

from apps.products.models import ProductBatch
from apps.contract.models import StockEntry, StockEntryItem
from apps.products.utils.barcode_utility import generate_unique_barcode


class StockEntryService:

    @staticmethod
    @transaction.atomic
    def create_entry(*, supplier, store, items, user):

        # 🔹 create header
        entry = StockEntry.objects.create(
            supplier=supplier,
            store=store,
            created_by=user
        )

        item_objs = []

        for item in items:
            product = item["product"]
            quantity = item["quantity"]
            purchase_price = item["purchase_price"]
            selling_price = item["selling_price"]

            from django.db.models import F

            batch = ProductBatch.objects.select_for_update().filter(
                store=store,
                product=product
            ).first()

            if batch:
                # 🔥 MUHIM: barcode o‘zgarmaydi
                ProductBatch.objects.filter(id=batch.pk).update(
                    quantity=F("quantity") + quantity,
                    purchase_price=purchase_price,
                    selling_price=selling_price
                )
            else:
                # ➕ faqat birinchi marta create
                ProductBatch.objects.create(
                    product=product,
                    store=store,
                    quantity=quantity,
                    purchase_price=purchase_price,
                    selling_price=selling_price,
                    barcode=generate_unique_barcode()  # 🔥 faqat shu yerda
                )

            # 🔒 LOCK
            # batch = ProductBatch.objects.select_for_update().filter(
            #     store=store,
            #     product=product
            # ).first()
            #
            # if batch:
            #     # MERGE
            #     ProductBatch.objects.filter(id=batch.pk).update( # id -> pk
            #         quantity=F("quantity") + quantity,
            #         purchase_price=purchase_price,
            #         selling_price=selling_price
            #     )
            # else:
            #     # ➕ CREATE
            #     ProductBatch.objects.create(
            #         product=product,
            #         store=store,
            #         quantity=quantity,
            #         purchase_price=purchase_price,
            #         selling_price=selling_price
            #     )

            item_objs.append(
                StockEntryItem(
                    entry=entry,
                    product=product,
                    quantity=quantity,
                    purchase_price=purchase_price,
                    selling_price=selling_price
                )
            )

        # BULK INSERT (safe, chunki signal kerak emas)
        StockEntryItem.objects.bulk_create(item_objs)

        return entry
