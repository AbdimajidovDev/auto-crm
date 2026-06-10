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

        # ✅ YAXSHI: Transfer yaratish jarayoni `transaction.atomic` ichida, yarimta yozuv qolib ketish xavfi kamaygan.
        transfer = StockTransfer.objects.create(
            from_store=from_store,
            to_store=to_store,
            created_by=user
        )

        for item in items_data:
            # ⚠️ MUAMMO [PERFORMANCE]: Har item uchun alohida `ProductBatch.objects...get()` so'rovi ishlaydi.
            # Sabab: itemlar oldindan bitta queryset bilan product_id bo'yicha xaritalanmagan.
            # Natija: transferda ko'p item bo'lsa N ta SELECT va N ta INSERT paydo bo'ladi.
            # ✅ YECHIM:
            # product_ids = [item["product"].id for item in items_data]
            # batches = {
            #     batch.product_id: batch
            #     for batch in ProductBatch.objects.select_for_update().filter(store=from_store, product_id__in=product_ids)
            # }
            # item_objs = [StockTransferItem(stock_transfer=transfer, product=item["product"], quantity=item["quantity"], ...) for item in items_data]
            # StockTransferItem.objects.bulk_create(item_objs)
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

        # ⚠️ MUAMMO [PERFORMANCE/N+1]: `items`, `from_store`, `to_store` oldindan yuklanmagan.
        # Sabab: pastda `transfer.items.all()`, `transfer.from_store`, `transfer.to_store` ishlatiladi.
        # Natija: approve jarayonida itemlar soniga bog'liq qo'shimcha querylar va FK lookup paydo bo'ladi.
        # ✅ YECHIM:
        # transfer = (
        #     StockTransfer.objects
        #     .select_for_update()
        #     .select_related("from_store", "to_store")
        #     .prefetch_related("items__product")
        #     .get(id=transfer_id)
        # )
        transfer = StockTransfer.objects.select_for_update().get(id=transfer_id)

        TransferService._validate_transfer_action(user, transfer)

        if transfer.status != StockTransfer.Status.PENDING:
            raise ValidationError("Transfer yakunlangan")

        for item in transfer.items.all():

            # ⚠️ MUAMMO [KRITIK/PERFORMANCE]: Loop ichida `get_or_create` va `save()` ko'p martalik DB write qiladi.
            # Sabab: source/target batchlar itemlar bo'yicha bulk tarzda tayyorlanmagan.
            # Natija: katta transferda lock va query soni ko'payadi, approve endpoint sekinlashadi.
            # ✅ YECHIM:
            # source_batches = ProductBatch.objects.select_for_update().filter(store=transfer.from_store, product_id__in=product_ids)
            # target_batches = ProductBatch.objects.select_for_update().filter(store=transfer.to_store, product_id__in=product_ids)
            # ProductBatch.objects.bulk_update(updated_batches, ["quantity"])
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
                    # 'barcode': generate_unique_barcode(),
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


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 2
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [create_transfer/approve_transfer loop ichidagi querylarni bulk/prefetch strategiyaga o'tkazish]
# ═══════════════════════════════
