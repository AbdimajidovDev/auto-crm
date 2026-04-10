from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.products.models import ProductBatch
from apps.products.utils.barcode_utility import generate_unique_barcode, generate_barcode_image
from apps.transfer.models import StockTransfer, StockTransferItem


class TransferService:

    @staticmethod
    @transaction.atomic
    def create_transfer(*, from_store, to_store, items_data, user):
        # 1. Transfer "shapkasi"
        transfer = StockTransfer.objects.create(
            from_store=from_store,
            to_store=to_store,
            created_by=user
        )

        # 2. Itemlarni tekshirish va yaratish
        for item in items_data:
            batch = ProductBatch.objects.filter(
                store=from_store,
                product=item['product']
            ).first()

            if not batch or batch.quantity < item['quantity']:
                raise ValidationError(f"{item['product'].name} uchun yetarli stock yo'q")

            StockTransferItem.objects.create(
                stock_transfer=transfer,
                product=item['product'],
                quantity=item['quantity'],
                purchase_price=batch.purchase_price,
                selling_price=batch.selling_price
            )

        return transfer

    @staticmethod
    @transaction.atomic
    def approve_transfer(*, transfer_id, user):
        transfer = StockTransfer.objects.select_for_update().get(id=transfer_id)
        if transfer.status != StockTransfer.Status.PENDING:
            raise ValidationError("Transfer yakunlangan")

        # Har bir item bo'yicha stockni o'zgartiramiz
        for item in transfer.items.all():
            # Source (Chiqish)
            source_batch = ProductBatch.objects.select_for_update().get(
                store=transfer.from_store, product=item.product
            )

            if source_batch.quantity < item.quantity:
                raise ValidationError(f"{item.product.name} yetishmayapti")

            source_batch.quantity -= item.quantity
            source_batch.save()

            # Target (Kirish)
            target_batch, created = ProductBatch.objects.get_or_create(
                store=transfer.to_store,
                product=item.product,
                defaults={
                    'purchase_price': item.purchase_price,
                    'selling_price': item.selling_price,
                    'barcode': generate_unique_barcode(),
                    'quantity': 0
                }
            )
            target_batch.quantity += item.quantity
            target_batch.save()

        transfer.status = StockTransfer.Status.APPROVED
        transfer.approved_by = user
        transfer.approved_at = timezone.now()
        transfer.save()
        return transfer


    @staticmethod
    def reject_transfer(*, transfer_id, user):
        transfer = StockTransfer.objects.get(id=transfer_id)

        if transfer.status != StockTransfer.Status.PENDING:
            raise ValidationError("Transfer allaqachon yakunlangan")

        transfer.status = StockTransfer.Status.REJECTED
        transfer.approved_by = user
        transfer.approved_at = timezone.now()
        transfer.save()

        return transfer



# class TransferService:
#
#     # 🔹 1. REQUEST YARATISH
#     @staticmethod
#     def create_transfer(*, from_store, to_store, product_id, quantity, user):
#
#         batch = ProductBatch.objects.filter(
#             store=from_store,
#             product_id=product_id
#         ).first()
#
#         if not batch:
#             raise ValidationError("Batch topilmadi")
#
#         if batch.quantity < quantity:
#             raise ValidationError("Yetarli stock yo‘q")
#
#         return StockTransfer.objects.create(
#             from_store=from_store,
#             to_store=to_store,
#             product_id=product_id.id,
#             quantity=quantity,
#             purchase_price=batch.purchase_price,
#             selling_price=batch.selling_price,
#             created_by=user
#         )
#
#
#     @staticmethod
#     @transaction.atomic
#     def approve_transfer(*, transfer_id, user):
#
#         transfer = StockTransfer.objects.select_for_update().get(id=transfer_id)
#
#         if transfer.status != StockTransfer.Status.PENDING:
#             raise ValidationError("Transfer allaqachon yakunlangan")
#
#         # 🔒 SOURCE LOCK
#         source_batch = ProductBatch.objects.select_for_update().filter(
#             store=transfer.from_store,
#             product=transfer.product
#         ).first()
#
#         if not source_batch:
#             raise ValidationError("Source batch topilmadi")
#
#         if source_batch.quantity < transfer.quantity:
#             raise ValidationError({
#                 "detail": "Yetarli stock yo‘q",
#                 "available": source_batch.quantity
#             })
#
#         # 🔻 deduct
#         ProductBatch.objects.filter(id=source_batch.pk).update(
#             quantity=F("quantity") - transfer.quantity
#         )
#
#         # 🔒 TARGET LOCK
#         target_batch = ProductBatch.objects.select_for_update().filter(
#             store=transfer.to_store,
#             product=transfer.product
#         ).first()
#
#         if target_batch:
#             # 🔥 MERGE + PRICE UPDATE
#             ProductBatch.objects.filter(id=target_batch.pk).update(
#                 quantity=F("quantity") + transfer.quantity,
#                 purchase_price=transfer.purchase_price,
#                 selling_price=transfer.selling_price
#             )
#         else:
#             # ➕ CREATE
#             barcode = generate_unique_barcode()
#
#             ProductBatch.objects.create(
#                 product=transfer.product,
#                 store=transfer.to_store,
#                 quantity=transfer.quantity,
#                 purchase_price=transfer.purchase_price,
#                 selling_price=transfer.selling_price,
#                 barcode=barcode,
#                 shtrix_code =generate_barcode_image(barcode)
#             )
#
#         # ✅ status update
#         transfer.status = StockTransfer.Status.APPROVED
#         transfer.approved_by = user
#         transfer.approved_at = timezone.now()
#         transfer.save()
#
#         return transfer
#

