from decimal import Decimal
from django.db import transaction
from django.db.models import F, Sum
from rest_framework.exceptions import ValidationError

from apps.debts.services import DebtService
from apps.inventory.models import InventorySession, InventoryMovement
from apps.inventory.services.inventory_hooks_service import handle_sale_return
from apps.products.models import Product, ProductBatch
from apps.sales.models import Sale, SaleReturn, SaleReturnItem, Payment



class SaleReturnService:

    @staticmethod
    @transaction.atomic
    def create_return(*, user, data):

        sale = (
            Sale.objects
            .select_for_update()
            .prefetch_related("items")
            .get(id=data["sale"])
        )

        store = sale.store
        customer = sale.customer

        return_obj = SaleReturn.objects.create(
            sale=sale,
            store=store,
            customer=customer,
            seller=user,
            comment=data.get("comment")
        )

        total_refund = Decimal("0")

        sale_items_map = {item.id: item for item in sale.items.all()}

        # 🔥 SESSIONNI 1 MARTA OLAMIZ (LOOP ICHIDA EMAS)
        session = InventorySession.objects.filter(
            store=store,
            status=InventorySession.Status.ACTIVE
        ).first()

        for item in data["items"]:

            sale_item = sale_items_map.get(item["sale_item"])
            if not sale_item:
                raise ValidationError("SaleItem topilmadi")

            quantity = item["quantity"]

            if quantity <= 0:
                raise ValidationError("Quantity > 0 bo‘lishi kerak")

            available = sale_item.quantity - sale_item.returned_quantity
            if quantity > available:
                raise ValidationError("Miqdor oshib ketdi")

            # =========================
            # 🔹 1. STOCK UPDATE
            # =========================
            ProductBatch.objects.filter(
                store=store,
                product=sale_item.product
            ).update(
                quantity=F("quantity") + quantity
            )

            # =========================
            # 🔹 2. SALE ITEM UPDATE
            # =========================
            sale_item.returned_quantity = F("returned_quantity") + quantity
            sale_item.save(update_fields=["returned_quantity"])

            # =========================
            # 🔹 3. INVENTORY MOVEMENT (FAKT 1 MARTA)
            # =========================
            if session:
                handle_sale_return(
                    return_obj=return_obj,
                    sale_item=sale_item,
                    quantity=quantity
                )

            # =========================
            # 🔹 4. RETURN ITEM
            # =========================
            refund_amount = sale_item.unit_price * quantity
            total_refund += refund_amount

            SaleReturnItem.objects.create(
                sale_return=return_obj,
                sale_item=sale_item,
                product=sale_item.product,
                quantity=quantity,
                unit_price=sale_item.unit_price,
                total_price=refund_amount
            )

        # =========================
        # 🔄 REFRESH
        # =========================
        sale.refresh_from_db()

        return_obj.total_refund = total_refund
        return_obj.save(update_fields=["total_refund"])

        # =========================
        # 💰 ACCOUNTING
        # =========================
        if customer:

            current_debt = DebtService.get_sale_debt(sale)

            if current_debt > 0:
                reduce_amount = min(current_debt, total_refund)

                if reduce_amount > 0:
                    DebtService.decrease_debt(
                        customer=customer,
                        sale=sale,
                        amount=reduce_amount
                    )

                remaining = total_refund - reduce_amount

                if remaining > 0:
                    Payment.objects.create(
                        customer=customer,
                        sale=sale,
                        amount=remaining,
                        type=Payment.Type.CASH
                    )

            else:
                Payment.objects.create(
                    customer=customer,
                    sale=sale,
                    amount=total_refund,
                    type=Payment.Type.CASH
                )

        # =========================
        # 📊 SALE STATUS UPDATE
        # =========================
        aggregated = sale.items.aggregate(
            total=Sum("quantity"),
            returned=Sum("returned_quantity")
        )

        if aggregated["total"] == aggregated["returned"]:
            sale.status = Sale.Status.RETURNED
            sale.save(update_fields=["status"])

        # 🔺 LOW STOCK: returns increased stock -> may resolve OPEN records.
        from apps.inventory.services import LowStockService
        returned_product_ids = [
            sale_items_map[item["sale_item"]].product_id for item in data["items"]
        ]
        LowStockService.schedule_evaluation(store=store, product_ids=returned_product_ids)

        return return_obj
    


# class SaleReturnService:
#
#     @staticmethod
#     @transaction.atomic
#     def create_return(*, user, data):
#
#         sale = (
#             Sale.objects
#             .select_for_update()
#             .prefetch_related("items")
#             .get(id=data["sale"])
#         )
#
#         store = sale.store
#         customer = sale.customer
#
#         return_obj = SaleReturn.objects.create(
#             sale=sale,
#             store=store,
#             customer=customer,
#             seller=user,
#             comment=data.get("comment")
#         )
#
#         total_refund = Decimal("0")
#
#         sale_items_map = {
#             item.id: item for item in sale.items.all()
#         }
#
#         for item in data["items"]:
#             sale_item = sale_items_map.get(item["sale_item"])
#
#             if not sale_item:
#                 raise ValidationError("SaleItem topilmadi")
#
#             available = sale_item.quantity - sale_item.returned_quantity
#
#             if item["quantity"] <= 0:
#                 raise ValidationError("Quantity > 0 bo‘lishi kerak")
#
#             if item["quantity"] > available:
#                 raise ValidationError("Miqdor oshib ketdi")
#
#             # 🔒 LOCK PRODUCT (critical)
#             # product = ProductBatch.objects.select_for_update().get(id=sale_item.product_id)
#
#             product = ProductBatch.objects.filter(
#                 store=sale.store,
#                 product=sale_item.product
#             ).update(
#                 quantity=F("quantity") + item["quantity"]
#             )
#
#             # product quantity update dan keyin
#
#             handle_sale_return(
#                 return_obj=return_obj,
#                 sale_item=sale_item,
#                 quantity=item["quantity"]
#             )
#
#
#             # ✅ STOCK RETURN (F expression → race safe)
#             # product.quantity = F("quantity") + item["quantity"]
#             # product.save(update_fields=["quantity"])
#
#             # 🔍 ACTIVE SESSION TOPAMIZ
#             session = InventorySession.objects.filter(
#                 store=sale.store,
#                 status="active"
#             ).first()
#
#             if session:
#                 InventoryMovement.objects.create(
#                     session=session,
#                     product=sale_item.product,
#                     quantity=item["quantity"],
#                     type=InventoryMovement.Type.RETURN,
#                     ref_id=return_obj.id
#                 )
#
#             # ✅ RETURNED QUANTITY UPDATE
#             sale_item.returned_quantity = F("returned_quantity") + item["quantity"]
#             sale_item.save(update_fields=["returned_quantity"])
#
#             refund_amount = sale_item.unit_price * item["quantity"]
#             total_refund += refund_amount
#
#             SaleReturnItem.objects.create(
#                 sale_return=return_obj,
#                 sale_item=sale_item,
#                 product=sale_item.product,
#                 quantity=item["quantity"],
#                 unit_price=sale_item.unit_price,
#                 total_price=refund_amount
#             )
#
#         # 🔄 refresh sale_items (F expression ishlatilgani uchun)
#         sale.refresh_from_db()
#
#         return_obj.total_refund = total_refund
#         return_obj.save(update_fields=["total_refund"])
#
#         # =========================
#         # 💰 ACCOUNTING
#         # =========================
#         if customer:
#
#             current_debt = DebtService.get_sale_debt(sale)
#
#             if current_debt > 0:
#                 reduce_amount = min(current_debt, total_refund)
#
#                 if reduce_amount > 0:
#                     DebtService.decrease_debt(
#                         customer=customer,
#                         sale=sale,
#                         amount=reduce_amount
#                     )
#
#                 remaining = total_refund - reduce_amount
#
#                 if remaining > 0:
#                     Payment.objects.create(
#                         customer=customer,
#                         sale=sale,
#                         amount=remaining,
#                         type=Payment.Type.CASH
#                     )
#
#             else:
#                 Payment.objects.create(
#                     customer=customer,
#                     sale=sale,
#                     amount=total_refund,
#                     type=Payment.Type.CASH
#                 )
#
#         # =========================
#         # 📊 SALE STATUS UPDATE
#         # =========================
#         aggregated = sale.items.aggregate(
#             total=Sum("quantity"),
#             returned=Sum("returned_quantity")
#         )
#
#         if aggregated["total"] == aggregated["returned"]:
#             sale.status = Sale.Status.RETURNED
#         else:
#             # partial return → statusni o‘zgartirmaymiz
#             pass
#
#         sale.save(update_fields=["status"])
#
#         return return_obj
