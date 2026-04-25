from rest_framework.exceptions import ValidationError

from apps.inventory.services.inventory_hooks_service import handle_transfer_approved, handle_transfer_in
from apps.products.utils.barcode_utility import generate_unique_barcode
from rest_framework.exceptions import PermissionDenied


from django.db import transaction
from django.utils import timezone
from django.core.exceptions import PermissionDenied

from apps.transfer.models import StockTransfer, StockTransferItem
from apps.products.models import ProductBatch


class TransferService:

    @staticmethod
    def _validate_transfer_action(user, transfer):
        if user.is_superuser:
            return

        can_access_to = user.store_links.filter(
            store=transfer.to_store,
            is_active=True
        ).exists()

        if not can_access_to:
            raise PermissionDenied(
                "Siz ushbu transferni boshqara olmaysiz"
            )

    @staticmethod
    def _validate_permissions(user, from_store):
        if user.is_superuser:
            return

        is_assigned = user.store_links.filter(
            store=from_store,
            is_active=True
        ).exists()

        if not is_assigned:
            raise PermissionDenied(
                "Siz faqat o'zingizga biriktirilgan storedan transfer qila olasiz"
            )

    # =========================
    # CREATE
    # =========================
    @staticmethod
    @transaction.atomic
    def create_transfer(*, from_store, to_store, items_data, user):

        TransferService._validate_permissions(user, from_store)

        transfer = StockTransfer.objects.create(
            from_store=from_store,
            to_store=to_store,
            created_by=user
        )

        for item in items_data:
            batch = ProductBatch.objects.select_for_update().get(
                store=from_store,
                product=item['product']
            )

            if batch.quantity < item['quantity']:
                raise ValidationError(
                    f"{item['product'].name} uchun yetarli stock yo'q"
                )

            StockTransferItem.objects.create(
                stock_transfer=transfer,
                product=item['product'],
                quantity=item['quantity'],
                purchase_price=batch.purchase_price,
                selling_price=batch.selling_price
            )

        # 🔥 EVENT

        from apps.transfer.services import NotificationService
        NotificationService.notify_transfer_created(transfer)

        return transfer

    # =========================
    # APPROVE
    # =========================
    @staticmethod
    @transaction.atomic
    def approve_transfer(*, transfer_id, user):

        transfer = StockTransfer.objects.select_for_update().get(id=transfer_id)

        TransferService._validate_transfer_action(user, transfer)

        if transfer.status != StockTransfer.Status.PENDING:
            raise ValidationError("Transfer yakunlangan")

        for item in transfer.items.all():

            source_batch = ProductBatch.objects.select_for_update().get(
                store=transfer.from_store,
                product=item.product
            )

            if source_batch.quantity < item.quantity:
                raise ValidationError(f"{item.product.name} yetishmayapti")

            source_batch.quantity -= item.quantity
            source_batch.save()

            target_batch, _ = ProductBatch.objects.get_or_create(
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

        handle_transfer_approved(transfer)  # OUT
        handle_transfer_in(transfer)  # 🔥 IN
        

        return transfer

    # =========================
    # REJECT
    # =========================
    @staticmethod
    @transaction.atomic
    def reject_transfer(*, transfer_id, user):

        # 🔥 race condition fix
        transfer = StockTransfer.objects.select_for_update().get(id=transfer_id)

        TransferService._validate_transfer_action(user, transfer)

        if transfer.status != StockTransfer.Status.PENDING:
            raise ValidationError("Transfer allaqachon yakunlangan")

        transfer.status = StockTransfer.Status.REJECTED
        transfer.approved_by = user
        transfer.approved_at = timezone.now()
        transfer.save()

        # 🔥 EVENT

        from apps.transfer.services import NotificationService
        NotificationService.notify_transfer_rejected(transfer)

        return transfer

