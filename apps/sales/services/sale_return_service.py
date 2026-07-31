import uuid
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from django.db.models import F, Sum
from rest_framework.exceptions import NotFound, ValidationError

from apps.common.store_scope import ensure_store_access
from apps.debts.services import DebtService
from apps.inventory.models import InventorySession, InventoryMovement
from apps.inventory.services.inventory_hooks_service import handle_sale_return
from apps.products.models import Product, ProductBatch
from apps.sales.models import Sale, SaleReturn, SaleReturnItem, Payment
from apps.sales.services.payment_service import SalePaymentService



class SaleReturnService:

    @staticmethod
    @transaction.atomic
    def create_return(*, user, data):

        try:
            sale = (
                Sale.objects
                .select_for_update()
                .prefetch_related("items")
                .get(id=data["sale"])
            )
        except Sale.DoesNotExist:
            raise NotFound("Sotuv topilmadi")

        store = sale.store
        customer = sale.customer

        # Qaytarish sotuv qaysi do'konda bo'lgan bo'lsa, o'sha do'kon xodimi
        # tomonidan amalga oshirilishi kerak. Busiz A do'kondagi sotuvchi
        # B do'konning kassasidan pul qaytartira olardi.
        ensure_store_access(user, sale.store_id, "Siz faqat o'z do'koningiz sotuvini qaytara olasiz")

        return_obj = SaleReturn.objects.create(
            sale=sale,
            store=store,
            customer=customer,
            seller=user,
            comment=data.get("comment")
        )

        total_refund = Decimal("0")

        sale_items_map = {item.id: item for item in sale.items.all()}

        # Bitta so'rovda bir sale_item ikki marta kelsa: miqdorlar birlashtiriladi.
        # Aks holda ikkinchi aylanishda `returned_quantity` F() ifodasi bo'lib
        # qolib, `quantity > available` taqqoslashi TypeError bilan yiqilardi.
        merged_items = {}
        for raw in data["items"]:
            item_id = raw["sale_item"]
            merged_items[item_id] = merged_items.get(item_id, 0) + raw["quantity"]

        # Sotuv darajasidagi chegirma har bir qatorga proportsional taqsimlanadi:
        # mijoz chegirma bilan to'lagan, demak qaytarim ham chegirmali bo'lishi kerak.
        # Bunsiz 30% chegirmali sotuvni to'liq qaytarganda do'kon olmagan pulini qaytarardi.
        gross_total = sum(
            (si.unit_price * si.quantity for si in sale_items_map.values()),
            Decimal("0"),
        )
        discount_amount = sale.discount_amount or Decimal("0")
        if gross_total > 0 and discount_amount > 0:
            refund_ratio = (gross_total - discount_amount) / gross_total
        else:
            refund_ratio = Decimal("1")

        # 🔥 SESSIONNI 1 MARTA OLAMIZ (LOOP ICHIDA EMAS)
        session = InventorySession.objects.filter(
            store=store,
            status=InventorySession.Status.ACTIVE
        ).first()

        for sale_item_id, quantity in merged_items.items():

            sale_item = sale_items_map.get(sale_item_id)
            if not sale_item:
                raise ValidationError("SaleItem topilmadi")

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
            # Chegirma nisbati qo'llanadi (yuqoridagi refund_ratio izohiga qarang)
            refund_amount = (sale_item.unit_price * quantity * refund_ratio).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
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
        # Avval qaytarim sotuv qarzini kamaytiradi, qolgan qismi mijozga PUL bilan
        # qaytariladi. Pul qismi sotuvdagi kabi erkin taqsimlanadi (payments[]):
        # naqd / karta / aralash. payments yuborilmasa — eski xatti-harakat: hammasi naqd.
        money_refund = total_refund

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

                money_refund = total_refund - reduce_amount

        refund_payments = data.get("payments")

        # Pul qaytarilgan bo'lsa Payment qatorlari shu guruh bilan yoziladi va
        # guruh return_obj ga saqlanadi — detailda "qaysi kartadan qancha
        # qaytarildi" aniq shu qaytarimga bog'lab ko'rsatiladi
        refund_group = None

        if refund_payments:
            refund_payments = [dict(p) for p in refund_payments]
            refund_total = sum(
                (Decimal(str(p["amount"])) for p in refund_payments), Decimal("0")
            )
            # Mijoz tomoni chegirma nisbatini o'zi hisoblaydi (chegirmali sotuvda
            # qaytarim = narx × miqdor × nisbat), shuning uchun yaxlitlashda bir
            # necha tiyinlik farq bo'lishi mumkin. Bunday farq uchun qaytarimni
            # rad etish noto'g'ri — 1 so'mgacha farqga yo'l qo'yiladi va farq
            # oxirgi qatorga qo'shilib, yozuvlar yig'indisi server hisoblagan
            # summaga AYNAN tenglashtiriladi.
            drift = money_refund - refund_total
            if abs(drift) > Decimal("1"):
                raise ValidationError({
                    "payments": (
                        f"Qaytariladigan to'lovlar jami {refund_total} — "
                        f"pul bilan qaytarilishi kerak bo'lgan summa {money_refund} ga teng bo'lishi shart"
                    )
                })
            if drift != 0:
                last = refund_payments[-1]
                adjusted = Decimal(str(last["amount"])) + drift
                if adjusted <= 0:
                    raise ValidationError({
                        "payments": "To'lov qatori summasi yaxlitlashdan keyin musbat bo'lishi kerak"
                    })
                last["amount"] = adjusted

            refund_group = uuid.uuid4()
            SalePaymentService.record_payments(
                sale=sale,
                payments_data=refund_payments,
                customer=customer,
                is_refund=True,
                payment_group=refund_group,
            )

        elif money_refund > 0:
            # payments[] yuborilmagan — pul qismi to'liq naqd qaytarilgan hisoblanadi.
            # DIQQAT: bu yerda ilgari `customer and ...` sharti bor edi va mijozsiz
            # (walk-in) sotuvni naqd qaytarganda Payment umuman yozilmasdi — kassadan
            # pul chiqqani hech qayerda ko'rinmasdi (qaytarim detalida ham,
            # to'lovlar/NET hisobotida ham). Qaytarim mijoz ro'yxatdan o'tgan-o'tmaganiga
            # bog'liq emas: pul kassadan chiqqan bo'lsa, yozuv bo'lishi shart.
            refund_group = uuid.uuid4()
            SalePaymentService.record_payments(
                sale=sale,
                payments_data=[{"type": Payment.Type.CASH, "amount": money_refund}],
                customer=customer,
                is_refund=True,
                payment_group=refund_group,
            )

        if refund_group is not None:
            return_obj.payment_group = refund_group
            return_obj.save(update_fields=["payment_group"])

        # To'lovlar o'zgardi — sotuvning to'lov turi markaziy metod bilan qayta hisoblanadi
        sale.recalculate_payment_type()

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
