from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.products.models import ProductBatch
from apps.transfer.models import StockTransfer


class TransferService:

    # 🔹 1. REQUEST YARATISH
    @staticmethod
    def create_transfer(*, from_store, to_store, product_id, quantity, user):

        batch = ProductBatch.objects.filter(
            store=from_store,
            product_id=product_id
        ).first()

        if not batch:
            raise ValidationError("Batch topilmadi")

        if batch.quantity < quantity:
            raise ValidationError("Yetarli stock yo‘q")

        return StockTransfer.objects.create(
            from_store=from_store,
            to_store=to_store,
            product_id=product_id.id,
            quantity=quantity,
            purchase_price=batch.purchase_price,
            selling_price=batch.selling_price,
            created_by=user
        )


    @staticmethod
    @transaction.atomic
    def approve_transfer(*, transfer_id, user):

        transfer = StockTransfer.objects.select_for_update().get(id=transfer_id)

        if transfer.status != StockTransfer.Status.PENDING:
            raise ValidationError("Transfer allaqachon yakunlangan")

        # 🔒 SOURCE LOCK
        source_batch = ProductBatch.objects.select_for_update().filter(
            store=transfer.from_store,
            product=transfer.product
        ).first()

        if not source_batch:
            raise ValidationError("Source batch topilmadi")

        if source_batch.quantity < transfer.quantity:
            raise ValidationError({
                "detail": "Yetarli stock yo‘q",
                "available": source_batch.quantity
            })

        # 🔻 deduct
        ProductBatch.objects.filter(id=source_batch.id).update(
            quantity=F("quantity") - transfer.quantity
        )

        # 🔒 TARGET LOCK
        target_batch = ProductBatch.objects.select_for_update().filter(
            store=transfer.to_store,
            product=transfer.product
        ).first()

        if target_batch:
            # 🔥 MERGE + PRICE UPDATE
            ProductBatch.objects.filter(id=target_batch.id).update(
                quantity=F("quantity") + transfer.quantity,
                purchase_price=transfer.purchase_price,
                selling_price=transfer.selling_price
            )
        else:
            # ➕ CREATE
            ProductBatch.objects.create(
                product=transfer.product,
                store=transfer.to_store,
                quantity=transfer.quantity,
                purchase_price=transfer.purchase_price,
                selling_price=transfer.selling_price,
            )

        # ✅ status update
        transfer.status = StockTransfer.Status.APPROVED
        transfer.approved_by = user
        transfer.approved_at = timezone.now()
        transfer.save()

        return transfer

    # 🔹 2. APPROVE (CORE LOGIC)
    # @staticmethod
    # @transaction.atomic
    # def approve_transfer(*, transfer_id, user):
    #
    #     transfer = StockTransfer.objects.select_for_update().get(id=transfer_id)
    #
    #     if transfer.status != StockTransfer.Status.PENDING:
    #         raise ValidationError("Transfer allaqachon yakunlangan")
    #
    #     # 🔒 source batch lock
    #     source_batch = ProductBatch.objects.select_for_update().filter(
    #         store=transfer.from_store,
    #         product=transfer.product,
    #         purchase_price=transfer.purchase_price,
    #         selling_price=transfer.selling_price
    #     ).first()
    #
    #     if not source_batch or source_batch.quantity < transfer.quantity:
    #         raise ValidationError("Stock yetarli emas")
    #
    #     # 🔻 deduct
    #     ProductBatch.objects.filter(id=source_batch.id).update(
    #         quantity=F("quantity") - transfer.quantity
    #     )
    #
    #     # 🔒 target batch
    #     target_batch = ProductBatch.objects.select_for_update().filter(
    #         store=transfer.to_store,
    #         product=transfer.product,
    #         purchase_price=transfer.purchase_price,
    #         selling_price=transfer.selling_price
    #     ).first()
    #
    #     if target_batch:
    #         ProductBatch.objects.filter(id=target_batch.id).update(
    #             quantity=F("quantity") + transfer.quantity
    #         )
    #     else:
    #         ProductBatch.objects.create(
    #             product=transfer.product,
    #             store=transfer.to_store,
    #             quantity=transfer.quantity,
    #             purchase_price=transfer.purchase_price,
    #             selling_price=transfer.selling_price,
    #         )
    #
    #     # ✅ status update
    #     transfer.status = StockTransfer.Status.APPROVED
    #     transfer.approved_by = user
    #     transfer.approved_at = timezone.now()
    #     transfer.save()
    #
    #     return transfer

    # 🔹 3. REJECT

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