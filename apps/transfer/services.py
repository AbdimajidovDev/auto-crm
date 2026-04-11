from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.products.models import ProductBatch
from apps.products.utils.barcode_utility import generate_unique_barcode
from apps.transfer.models import StockTransfer, StockTransferItem
from rest_framework.exceptions import PermissionDenied



class TransferService:

    @staticmethod
    def _validate_transfer_action(user, transfer):
        """
        Tasdiqlash yoki Rad etish uchun huquqlarni tekshirish
        """
        if user.is_superuser:
            return

        # Foydalanuvchi yo from_store ga, yo to_store ga biriktirilgan bo'lishi kerak
        can_access_to = user.store_links.filter(store=transfer.to_store, is_active=True).exists()

        if not can_access_to:
            raise PermissionDenied(
                "Sizga ushbu transferni boshqarish uchun ruxsat berilmagan. "
                "Siz transferni qabul qiluvchi do'konga biriktirilgan bo'lishingiz kerak."
            )


    @staticmethod
    def _validate_permissions(user, from_store):
        """
        Production darajasidagi ruxsat tekshiruvi:
        1. Superuser uchun cheklov yo'q.
        2. Seller/Manager uchun: ular ushbu do'konga biriktirilgan bo'lishi va aktiv bo'lishi shart.
        """
        # 1. Superuser uchun hech qanday cheklov yo'q
        if user.is_superuser:
            return

        # 2. Foydalanuvchi ushbu do'konga biriktirilganligini tekshirish
        is_assigned = user.store_links.filter(
            store=from_store,
            is_active=True
        ).exists()

        if not is_assigned:
            raise PermissionDenied(
                "Sizga faqat o'zingiz biriktirilgan do'kondan transfer qilishga ruxsat berilgan."
            )


    @staticmethod
    @transaction.atomic
    def create_transfer(*, from_store, to_store, items_data, user):
        # 0. Ruxsatlarni tekshirish
        TransferService._validate_permissions(user, from_store)

        # 1. Transfer "shapkasi"
        transfer = StockTransfer.objects.create(
            from_store=from_store,
            to_store=to_store,
            created_by=user
        )

        # 2. Itemlarni yaratish (oldingi logikangiz)
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

        TransferService._validate_transfer_action(user, transfer)

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

        TransferService._validate_transfer_action(user, transfer)

        if transfer.status != StockTransfer.Status.PENDING:
            raise ValidationError("Transfer allaqachon yakunlangan")

        transfer.status = StockTransfer.Status.REJECTED
        transfer.approved_by = user
        transfer.approved_at = timezone.now()
        transfer.save()

        return transfer



#
# class TransferService:
#
#     @staticmethod
#     @transaction.atomic
#     def create_transfer(*, from_store, to_store, items_data, user):
#         # 1. Transfer "shapkasi"
#         transfer = StockTransfer.objects.create(
#             from_store=from_store,
#             to_store=to_store,
#             created_by=user
#         )
#
#         # 2. Itemlarni tekshirish va yaratish
#         for item in items_data:
#             batch = ProductBatch.objects.filter(
#                 store=from_store,
#                 product=item['product']
#             ).first()
#
#             if not batch or batch.quantity < item['quantity']:
#                 raise ValidationError(f"{item['product'].name} uchun yetarli stock yo'q")
#
#             StockTransferItem.objects.create(
#                 stock_transfer=transfer,
#                 product=item['product'],
#                 quantity=item['quantity'],
#                 purchase_price=batch.purchase_price,
#                 selling_price=batch.selling_price
#             )
#
#         return transfer
