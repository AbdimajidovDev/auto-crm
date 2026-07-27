from decimal import Decimal

from rest_framework.generics import get_object_or_404

from apps.inventory.services.inventory_hooks_service import handle_sale_item
from apps.sales.models import Sale, SaleItem, Payment
from apps.sales.services.payment_service import SalePaymentService
from apps.products.models import Product, ProductBatch
from apps.debts.services import DebtService
from apps.store.models import StoreUser
from apps.users.models.customers import Customer

from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from apps.store.models import Store


class SaleService:

    @staticmethod
    @transaction.atomic
    def create_sale(*, user, data):
        # ⚠️ MUAMMO [ARXITEKTURA]: `create_sale` metodi bir nechta mas'uliyatni bajaryapti.
        # Sabab: store aniqlash, customer lookup, stock kamaytirish, discount hisoblash, payment yaratish,
        # sale status va debt yozish bitta uzun metod ichida.
        # Natija: testlash qiyinlashadi, xatolikda regressiya xavfi oshadi.
        # ✅ YECHIM:
        # store = SaleStoreResolver.resolve(user=user, store_id=data.get("store"))
        # totals = SalePricingService.calculate(items=items_data, discount_type=discount_type, discount_value=discount_value)
        # SaleStockService.reserve_items(store=store, items=items_data)
        # SalePaymentService.create_payments(sale=sale, payments=payments_data)
        items_data = data["items"]
        payments_data = data["payments"]
        discount_type = data.get("discount_type")
        discount_value = data.get("discount_value", Decimal("0"))

        # 🔴 STORE LOGIC
        if user.is_superuser:
            store = get_object_or_404(Store, id=data["store"])

        else:
            store_link = StoreUser.objects.filter(
                user=user,
                is_active=True
            ).select_related("store").first()

            if not store_link:
                raise ValidationError("Siz hech qaysi do‘konga biriktirilmagansiz")

            store = store_link.store
        customer = None

        if data.get("customer"):
            customer = get_object_or_404(Customer, id=data["customer"])

        # Sotuvni yaratish

        # Arxivlangan (status!='a') mahsulot sotilmaydi — POS keshi eskirgan
        # bo'lsa ham server darajasida bloklanadi
        sale_product_ids = [item["product"] for item in items_data]
        archived_names = list(
            Product.objects
            .filter(id__in=sale_product_ids)
            .exclude(status=Product.ProductStatus.ACTIVE)
            .values_list("name", flat=True)
        )
        if archived_names:
            raise ValidationError(
                f"Arxivlangan mahsulotni sotib bo'lmaydi: {', '.join(archived_names)}"
            )

        sale = Sale.objects.create(
            store=store,
            customer=customer,
            seller=user,
            discount_type=discount_type,
            discount_value=discount_value
        )

        subtotal = Decimal("0")

        for item in items_data:
            # ⚠️ MUAMMO [KRITIK/PERFORMANCE]: Loop ichida har item uchun batch lookup, UPDATE, SaleItem create va hook chaqirilmoqda.
            # Sabab: kerakli ProductBatch yozuvlari oldindan `product_id__in` bilan lock qilinmagan va SaleItemlar bulk yaratilmagan.
            # Natija: ko'p itemli sotuvda transaction uzoq davom etadi, lock contention va N ta query paydo bo'ladi.
            # ✅ YECHIM:
            # product_ids = [item["product"] for item in items_data]
            # batches = ProductBatch.objects.select_for_update().filter(store=store, product_id__in=product_ids, quantity__gt=0)
            # SaleItem.objects.bulk_create(item_objs)
            # ProductBatch.objects.bulk_update(updated_batches, ["quantity"])
            # PERFORMANCE: har bir savdo pozitsiyasi uchun `select_for_update` + alohida UPDATE —
            # ko'p qatorli savdoda tranzaksiya uzoq yopilib qolishi mumkin; batch strategiya yoki
            # birlashtirilgan stock update ko'rib chiqiladi.
            product_id = item["product"]
            quantity_to_sell = item["quantity"]
            price = item["price"]

            batch = ProductBatch.objects.select_for_update().filter(
                store=store,
                product_id=product_id,
                quantity__gt=0
            ).order_by("created_at").first()


            if not batch:
                raise ValidationError("Mahsulot mavjud emas")

            if batch.quantity < quantity_to_sell:
                raise ValidationError("Mahsulot yetarli emas")

            # 🔥 CRITICAL FIX
            purchase_price = batch.purchase_price

            ProductBatch.objects.filter(id=batch.pk).update(
                quantity=F('quantity') - quantity_to_sell
            )

            total_price = price * quantity_to_sell
            subtotal += total_price

            sale_item = SaleItem.objects.create(
                sale=sale,
                product_id=product_id,
                quantity=quantity_to_sell,
                unit_price=price,
                purchase_price=purchase_price,  # 🔥 YANGI
                total_price=total_price
            )

            handle_sale_item(sale_item)

        # 🔴 CALCULATE DISCOUNT (Chegirmani hisoblash)
        calculated_discount = Decimal("0")

        if discount_type == Sale.DiscountType.PERCENTAGE:
            # Foizli chegirma validatsiyasi (Serializarda ham bo'lishi mumkin)
            if discount_value > 100:
                raise ValidationError("Chegirma foizi 100 dan oshishi mumkin emas.")
            calculated_discount = (subtotal * discount_value) / Decimal("100")

        elif discount_type == Sale.DiscountType.FIXED:
            calculated_discount = discount_value

        # 🔥 ASOSIY VALIDATSIYA: Chegirma subtotaldan oshib ketsa xato berish
        if calculated_discount > subtotal:
            raise ValidationError({
                "discount_error": "Chegirma miqdori umumiy summadan oshib ketdi!",
                "subtotal": subtotal,
                "attempted_discount": calculated_discount
            })

        final_total_amount = subtotal - calculated_discount

        # 🔴 PAYMENTS — markaziy service orqali (bulk_create + bank_card invarianti)
        paid_amount = SalePaymentService.record_payments(
            sale=sale,
            payments_data=payments_data,
            customer=customer,
        )

        # 🔴 FINALIZE SALE
        sale.total_amount = final_total_amount
        sale.discount_amount = calculated_discount
        sale.paid_amount = paid_amount

        # Status logikasi
        if paid_amount >= final_total_amount:
            sale.status = Sale.Status.PAID
        elif paid_amount == 0:
            sale.status = Sale.Status.DEBT
        else:
            sale.status = Sale.Status.PARTIAL

        # To'lov turi (cash/card/mixed/debt) — markaziy metod; save=False,
        # chunki pastdagi sale.save() bilan bitta UPDATE da yoziladi
        sale.recalculate_payment_type(save=False)

        sale.save()

        # 🔴 DEBT
        # if customer and paid_amount < final_total_amount:
        #     DebtService.increase_debt(
        #         customer=customer,
        #         amount=final_total_amount - paid_amount,
        #         sale=sale
        #     )

        debt_due_date = data.get("debt_due_date")

        if customer and paid_amount < final_total_amount:
            DebtService.increase_debt(
                customer=customer,
                amount=final_total_amount - paid_amount,
                sale=sale,
                due_date=debt_due_date
            )

        # 🔻 LOW STOCK: sale reduced stock -> evaluate (after commit) for each product.
        from apps.inventory.services import LowStockService
        LowStockService.schedule_evaluation(
            store=store,
            product_ids=[item["product"] for item in items_data],
        )

        return sale


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 2
# Arxitektura muammolari: 1
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [create_sale ichidagi stock/payment yozuvlarini bulk strategiyaga ajratish va metodni kichik servicelarga bo'lish]
# ═══════════════════════════════
