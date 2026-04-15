from decimal import Decimal

from rest_framework.generics import get_object_or_404

from apps.sales.models import Sale, SaleItem, Payment
from apps.products.models import ProductBatch
from apps.debts.services import DebtService
from apps.store.models import Store
from apps.users.models.customers import Customer

from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from apps.store.models import Store


class SaleService:

    @staticmethod
    @transaction.atomic
    def create_sale(*, user, data):
        items_data = data["items"]
        payments_data = data["payments"]
        discount_type = data.get("discount_type")
        discount_value = data.get("discount_value", Decimal("0"))

        # 🔴 STORE LOGIC
        if user.is_superuser:
            if "store" not in data:
                raise ValidationError("Store required")
            store = get_object_or_404(Store, id=data["store"])
        else:
            store = user.store
            print('user.store', store)

        customer = None
        if data.get("customer"):
            customer = get_object_or_404(Customer, id=data["customer"])
            # customer = Customer.objects.get(id=data["customer"])

        # Sotuvni yaratish

        sale = Sale.objects.create(
            store=store,
            customer=customer,
            seller=user,
            discount_type=discount_type,
            discount_value=discount_value
        )

        subtotal = Decimal("0")

        # 🔴 ITEMS & STOCK LOGIC
        for item in items_data:
            product_id = item["product"]
            quantity_to_sell = item["quantity"]
            price = item["price"]

            # 1. Ombor qoldig'ini blokirovka qilib olish (Race condition oldini olish uchun)
            batch = ProductBatch.objects.select_for_update().filter(
                store=store,
                product_id=product_id
            ).first()

            # 2. VALIDATION: Mahsulot borligini va miqdorini tekshirish
            if not batch:
                raise ValidationError(f"Ushbu mahsulot do'konda mavjud emas.")

            if batch.quantity <= 0:
                raise ValidationError(f"{batch.product.name.upper()} mahsuloti tugagan (qoldiq 0).")

            if batch.quantity < quantity_to_sell:
                raise ValidationError(
                    f"{batch.product.name.upper()} mahsuloti yetarli emas. Do'konda {batch.quantity} dona mavjud! So'ralgan: {quantity_to_sell} dona."
                )

            # 3. STOCKNI KAMAYTIRISH (Atomar tarzda)
            # F() bazadagi qiymatni to'g'ridan-to'g'ri kamaytiradi
            ProductBatch.objects.filter(id=batch.id).update(
                quantity=F('quantity') - quantity_to_sell
            )

            total_price = price * quantity_to_sell
            subtotal += total_price

            SaleItem.objects.create(
                sale=sale,
                product_id=product_id,
                quantity=quantity_to_sell,
                unit_price=price,
                total_price=total_price
            )

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

        # 🔴 PAYMENTS
        paid_amount = Decimal("0")
        for p in payments_data:
            Payment.objects.create(
                sale=sale,
                customer=customer,
                amount=p["amount"],
                type=p["type"]
            )
            paid_amount += Decimal(str(p["amount"]))

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

        sale.save()

        # 🔴 DEBT
        if customer and paid_amount < final_total_amount:
            DebtService.increase_debt(
                customer=customer,
                amount=final_total_amount - paid_amount,
                sale=sale
            )

        return sale

