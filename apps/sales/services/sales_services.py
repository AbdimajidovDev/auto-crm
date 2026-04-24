from decimal import Decimal

from rest_framework.generics import get_object_or_404

from apps.inventory.services.inventory_hooks_service import handle_sale_item
from apps.sales.models import Sale, SaleItem, Payment
from apps.products.models import ProductBatch
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

        sale = Sale.objects.create(
            store=store,
            customer=customer,
            seller=user,
            discount_type=discount_type,
            discount_value=discount_value
        )

        subtotal = Decimal("0")

        for item in items_data:
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

            ProductBatch.objects.filter(id=batch.id).update(
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
