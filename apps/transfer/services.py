from django.db import transaction
from django.core.exceptions import ValidationError

from apps.products.models import ProductBatch
from apps.transfer.models import StockTransfer

from django.db import transaction

class TransferService:

    @staticmethod
    @transaction.atomic
    def transfer(*, from_store, to_store, barcode, quantity):

        # 🔒 LOCK SOURCE BATCH
        source_batch = ProductBatch.objects.select_for_update().get(
            store=from_store,
            barcode=barcode
        )

        if source_batch.quantity < quantity:
            raise ValidationError("Not enough stock")

        # 🔻 deduct
        source_batch.quantity -= quantity
        source_batch.save()

        # ➕ create new batch in target
        ProductBatch.objects.create(
            product=source_batch.product,
            store=to_store,
            quantity=quantity,
            purchase_price=source_batch.purchase_price,
            selling_price=source_batch.selling_price,
            barcode=source_batch.barcode,
        )

        # 📝 log transfer
        StockTransfer.objects.create(
            from_store=from_store,
            to_store=to_store,
            product=source_batch.product,
            quantity=quantity,
            purchase_price=source_batch.purchase_price,
            selling_price=source_batch.selling_price,
            barcode=barcode
        )