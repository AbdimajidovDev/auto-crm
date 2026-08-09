from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.inventory.models import InventorySession, StockAdjustment
from apps.products.models import ProductBatch


class StockAdjustmentService:
    """
    Bitta mahsulot qoldig'ini to'liq inventarizatsiyasiz to'g'irlash.

    Qoidalar:
      - Do'konda FAOL inventarizatsiya sessiyasi bo'lsa — bloklanadi (aks holda
        finalize() bu to'g'irlashni bosib ketardi yoki ikki marta hisoblanardi);
      - ProductBatch qatori select_for_update bilan qulflanadi — sotuv/transfer
        bilan poyga bo'lmaydi;
      - Har bir o'zgarish StockAdjustment yozuvi sifatida tarixda qoladi;
      - Qoldiq o'zgargach low-stock holati qayta baholanadi (kamaysa — ogohlantirish
        ochiladi, oshsa — mavjudi yopiladi).
    """

    @staticmethod
    @transaction.atomic
    def adjust(*, store_id, product_id, new_quantity, reason, comment="", user):
        if new_quantity < 0:
            raise ValidationError("Yangi miqdor manfiy bo'lishi mumkin emas.")

        if InventorySession.objects.filter(
            store_id=store_id,
            status=InventorySession.Status.ACTIVE,
        ).exists():
            raise ValidationError(
                "Bu do'konda faol inventarizatsiya sessiyasi bor — "
                "avval uni yakunlang yoki bekor qiling."
            )

        try:
            batch = ProductBatch.objects.select_for_update().get(
                store_id=store_id, product_id=product_id
            )
        except ProductBatch.DoesNotExist:
            raise ValidationError(
                "Bu do'konda mahsulot partiyasi (qoldig'i) mavjud emas — "
                "avval kirim yoki transfer qiling."
            )

        old_quantity = batch.quantity
        if new_quantity == old_quantity:
            raise ValidationError(
                "Yangi miqdor joriy qoldiq bilan bir xil — o'zgarish yo'q."
            )

        adjustment = StockAdjustment.objects.create(
            store_id=store_id,
            product_id=product_id,
            old_quantity=old_quantity,
            new_quantity=new_quantity,
            difference=new_quantity - old_quantity,
            reason=reason,
            comment=comment or "",
            created_by=user,
        )

        batch.quantity = new_quantity
        batch.save(update_fields=["quantity"])

        from apps.inventory.services.low_stock_service import LowStockService
        LowStockService.schedule_evaluation(store=store_id, product_ids=[product_id])

        return adjustment
