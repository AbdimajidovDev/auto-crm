from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.quantity import validate_quantity_step
from apps.inventory.models import InventorySession, StockAdjustment
from apps.products.models import Product, ProductBatch


class StockAdjustmentService:
    """
    Qo'lda qilingan qoldiq o'zgarishlari (Import va Hisobdan chiqarish) servisi.

    Asosiy tamoyillar:
      1. IMPORT (+): Qo'lda mahsulot qoldig'ini oshirish.
      2. WRITE_OFF (-): Qo'lda mahsulot qoldig'ini kamaytirish (qoldiq tekshiriladi).
      3. SNAPSHOT: Operatsiya paytidagi purchase_price va sale_price saqlanadi.
      4. CANCEL: O'chirilmaydi, status=CANCELLED, cancelled_by/cancelled_at yoziladi.
         - Import bekor qilinganda: stock yetarli bo'lsa kamaytiriladi (aks holda rad etiladi).
         - Write-off bekor qilinganda: stockga miqdor qaytariladi.
      5. ATOMICITY: Har bir amallar bitta transaction.atomic blokda.
      6. LOW-STOCK: Har bir stock o'zgarishida LowStockService qayta baholanadi.
    """

    @staticmethod
    @transaction.atomic
    def create_adjustment(
        *,
        store_id,
        product_id,
        quantity,
        type=StockAdjustment.Type.IMPORT,
        reason=None,
        comment="",
        user=None,
    ):
        """Yangi Import yoki Hisobdan chiqarish (Write-off) yaratadi va stockni yangilaydi."""
        if quantity is None or Decimal(str(quantity)) <= Decimal("0"):
            raise ValidationError("Miqdor 0 dan katta bo'lishi kerak.")

        try:
            product = Product.objects.select_related("unit_measurement").get(pk=product_id)
        except Product.DoesNotExist:
            raise ValidationError("Mahsulot topilmadi.")

        qty = validate_quantity_step(quantity, product=product)

        if InventorySession.objects.filter(
            store_id=store_id,
            status=InventorySession.Status.ACTIVE,
        ).exists():
            raise ValidationError(
                "Bu do'konda faol inventarizatsiya sessiyasi bor — "
                "avval uni yakunlang yoki bekor qiling."
            )

        # Batchni qulflaymiz (select_for_update)
        batch = ProductBatch.objects.select_for_update().filter(
            store_id=store_id, product_id=product_id
        ).first()

        if batch is None:
            if type == StockAdjustment.Type.WRITE_OFF:
                raise ValidationError("Bu do'konda mahsulot partiyasi (qoldig'i) mavjud emas.")
            # Import bo'lsa yangi partiya ochiladi
            other_batch = ProductBatch.objects.filter(product_id=product_id).first()
            p_price = other_batch.purchase_price if other_batch else Decimal("0")
            s_price = other_batch.selling_price if other_batch else Decimal("0")
            w_price = other_batch.wholesale_price if other_batch else Decimal("0")
            batch = ProductBatch.objects.create(
                store_id=store_id,
                product_id=product_id,
                quantity=Decimal("0"),
                purchase_price=p_price,
                selling_price=s_price,
                wholesale_price=w_price,
            )

        old_quantity = batch.quantity
        purchase_price = batch.purchase_price or Decimal("0")
        sale_price = batch.selling_price or Decimal("0")
        total_amount = (qty * purchase_price).quantize(Decimal("0.01"))

        if type == StockAdjustment.Type.IMPORT:
            new_quantity = old_quantity + qty
            difference = qty
            default_reason = StockAdjustment.Reason.MANUAL_IMPORT
        elif type == StockAdjustment.Type.WRITE_OFF:
            if old_quantity < qty:
                raise ValidationError(
                    f"Qoldiq yetarli emas (mavjud: {old_quantity}, chiqarilayotgan: {qty})."
                )
            new_quantity = old_quantity - qty
            difference = -qty
            default_reason = StockAdjustment.Reason.DAMAGED
        else:
            raise ValidationError(f"Noma'lum adjustment turi: {type}")

        batch.quantity = new_quantity
        batch.save(update_fields=["quantity"])

        adjustment = StockAdjustment.objects.create(
            store_id=store_id,
            product_id=product_id,
            type=type,
            status=StockAdjustment.Status.ACTIVE,
            quantity=qty,
            old_quantity=old_quantity,
            new_quantity=new_quantity,
            difference=difference,
            purchase_price=purchase_price,
            sale_price=sale_price,
            total_amount=total_amount,
            reason=reason or default_reason,
            comment=comment or "",
            created_by=user,
        )

        from apps.inventory.services.low_stock_service import LowStockService
        LowStockService.schedule_evaluation(store=store_id, product_ids=[product_id])

        return adjustment

    @staticmethod
    @transaction.atomic
    def cancel_adjustment(*, adjustment_id, user):
        """
        Operatsiyani (Import yoki Write-off) bekor qiladi va stockni qaytaradi.

        Xavfsizlik:
          - Agar Import bekor qilinsa, lekin tovar sotilgan/o'tkazilgan bo'lib,
            hozirgi qoldiq yetarli bo'lmasa — xato xabari beriladi va bekor qilinmaydi.
          - Yozuv bazadan o'chmaydi, status=CANCELLED ga o'tadi.
        """
        try:
            adjustment = StockAdjustment.objects.select_for_update().get(pk=adjustment_id)
        except StockAdjustment.DoesNotExist:
            raise ValidationError("Kiritilgan ID bo'yicha operatsiya topilmadi.")

        if adjustment.status == StockAdjustment.Status.CANCELLED:
            raise ValidationError("Ushbu operatsiya allaqachon bekor qilingan.")

        if InventorySession.objects.filter(
            store_id=adjustment.store_id,
            status=InventorySession.Status.ACTIVE,
        ).exists():
            raise ValidationError(
                "Bu do'konda faol inventarizatsiya sessiyasi bor — "
                "avval uni yakunlang yoki bekor qiling."
            )

        batch = ProductBatch.objects.select_for_update().filter(
            store_id=adjustment.store_id, product_id=adjustment.product_id
        ).first()

        if batch is None:
            raise ValidationError("Mahsulot partiyasi topilmadi.")

        qty = adjustment.quantity

        if adjustment.type == StockAdjustment.Type.IMPORT:
            if batch.quantity < qty:
                raise ValidationError(
                    f"Ushbu importdan keyin mahsulot bilan boshqa operatsiyalar bajarilgan va "
                    f"omborda yetarli qoldiq yo'q (mavjud: {batch.quantity}, kerak: {qty}). "
                    f"Importni bekor qilib bo'lmaydi."
                )
            batch.quantity = batch.quantity - qty
        elif adjustment.type == StockAdjustment.Type.WRITE_OFF:
            batch.quantity = batch.quantity + qty
        elif adjustment.type == StockAdjustment.Type.RECOUNT:
            if adjustment.difference > Decimal("0") and batch.quantity < adjustment.difference:
                raise ValidationError(
                    f"Ushbu operatsiyadan keyin mahsulot bilan boshqa amallar bajarilgan va "
                    f"omborda yetarli qoldiq yo'q. Bekor qilib bo'lmaydi."
                )
            batch.quantity = batch.quantity - adjustment.difference
        else:
            raise ValidationError(f"Noma'lum adjustment turi: {adjustment.type}")

        batch.save(update_fields=["quantity"])

        adjustment.status = StockAdjustment.Status.CANCELLED
        adjustment.cancelled_by = user
        adjustment.cancelled_at = timezone.now()
        adjustment.save(update_fields=["status", "cancelled_by", "cancelled_at"])

        from apps.inventory.services.low_stock_service import LowStockService
        LowStockService.schedule_evaluation(
            store=adjustment.store_id, product_ids=[adjustment.product_id]
        )

        return adjustment

    @staticmethod
    @transaction.atomic
    def adjust(*, store_id, product_id, new_quantity, reason=None, comment="", user=None):
        """Qoldiqni to'g'ridan-to'g'ri yangi miqdorga o'rnatish (legacy / recount)."""
        if new_quantity < 0:
            raise ValidationError("Yangi miqdor manfiy bo'lishi mumkin emas.")

        try:
            product = Product.objects.select_related("unit_measurement").get(pk=product_id)
        except Product.DoesNotExist:
            raise ValidationError("Mahsulot topilmadi.")

        new_qty = validate_quantity_step(new_quantity, product=product)

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
        if new_qty == old_quantity:
            raise ValidationError(
                "Yangi miqdor joriy qoldiq bilan bir xil — o'zgarish yo'q."
            )

        diff = new_qty - old_quantity
        adj_type = StockAdjustment.Type.IMPORT if diff > 0 else StockAdjustment.Type.WRITE_OFF
        qty = abs(diff)

        purchase_price = batch.purchase_price or Decimal("0")
        sale_price = batch.selling_price or Decimal("0")
        total_amount = (qty * purchase_price).quantize(Decimal("0.01"))

        adjustment = StockAdjustment.objects.create(
            store_id=store_id,
            product_id=product_id,
            type=adj_type,
            status=StockAdjustment.Status.ACTIVE,
            quantity=qty,
            old_quantity=old_quantity,
            new_quantity=new_qty,
            difference=diff,
            purchase_price=purchase_price,
            sale_price=sale_price,
            total_amount=total_amount,
            reason=reason or (StockAdjustment.Reason.MANUAL_IMPORT if diff > 0 else StockAdjustment.Reason.DAMAGED),
            comment=comment or "",
            created_by=user,
        )

        batch.quantity = new_qty
        batch.save(update_fields=["quantity"])

        from apps.inventory.services.low_stock_service import LowStockService
        LowStockService.schedule_evaluation(store=store_id, product_ids=[product_id])

        return adjustment

