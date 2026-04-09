from django.db import transaction
from decimal import Decimal

from rest_framework.exceptions import ValidationError

from apps.sales.models import Sale, SaleItem, Payment
from apps.products.models import Product
from apps.debts.services import DebtService
from apps.store.models import Store
from apps.users.models.customers import Customer


class SaleService:

    @staticmethod
    @transaction.atomic
    def create_sale(*, user, data):

        items_data = data["items"]
        payments_data = data["payments"]

        # 🔴 STORE LOGIC
        if user.is_superuser:
            if "store" not in data:
                raise ValidationError("Store required")
            store = Store.objects.get(id=data["store"])
        else:
            store = user.store  # ⚠️ assumption

        # 🔴 CUSTOMER
        customer = None
        if data.get("customer"):
            customer = Customer.objects.get(id=data["customer"])

        # 🔴 CREATE SALE
        sale = Sale.objects.create(
            store=store,
            customer=customer,
            seller=user,
            status=Sale.Status.PAID
        )

        total_amount = Decimal("0")

        # 🔴 ITEMS
        for item in items_data:
            product = Product.objects.get(id=item["product"])
            quantity = item["quantity"]
            price = item["price"]

            if quantity <= 0:
                raise ValidationError("Miqdor yaroqsiz")

            total_price = price * quantity
            total_amount += total_price

            # ❗ HOZIRCHA STOCK YO‘Q (keyin qo‘shamiz)

            SaleItem.objects.create(
                sale=sale,
                product=product,
                quantity=quantity,
                unit_price=price,
                total_price=total_price
            )

        # 🔴 PAYMENTS
        paid_amount = Decimal("0")

        for p in payments_data:
            amount = p["amount"]

            if amount <= 0:
                raise ValidationError("To‘lov miqdori noto‘g‘ri")

            Payment.objects.create(
                sale=sale,
                customer=customer,
                amount=amount,
                type=p["type"]
            )

            paid_amount += amount

        # 🔴 STATUS
        sale.total_amount = total_amount
        sale.paid_amount = paid_amount

        if paid_amount == total_amount:
            sale.status = Sale.Status.PAID

        elif paid_amount == 0:
            sale.status = Sale.Status.DEBT

        elif paid_amount < total_amount:
            sale.status = Sale.Status.PARTIAL

        else:
            raise ValidationError("Ortiqcha to'lovga yo'l qo'yilmaydi")

        sale.save()

        # 🔴 DEBT
        if customer and paid_amount < total_amount:
            DebtService.increase_debt(
                customer=customer,
                amount=total_amount - paid_amount,
                sale=sale
            )

        return sale




# class SaleService:
#
#     @staticmethod
#     @transaction.atomic
#     def create_sale(*, user, store, items: list, payments: list, customer=None):
#
#         # 🔴 1. CREATE SALE
#         sale = Sale.objects.create(
#             store=store,
#             customer=customer,
#             seller=user,
#             status=Sale.Status.PAID  # vaqtincha
#         )
#
#         total_amount = 0
#
#         # 🔴 2. ITEMS
#         for item in items:
#             product = item["product"]
#             quantity = item["quantity"]
#             price = item["price"]
#
#             total_price = quantity * price
#             total_amount += total_price
#
#             # ⚠️ BU YERDA STOCK CHECK QO‘SHILADI (keyin)
#
#             SaleItem.objects.create(
#                 sale=sale,
#                 product=product,
#                 quantity=quantity,
#                 unit_price=price,
#                 total_price=total_price
#             )
#
#         # 🔴 3. PAYMENTS
#         paid_amount = 0
#
#         for p in payments:
#             Payment.objects.create(
#                 sale=sale,
#                 amount=p["amount"],
#                 type=p["type"]
#             )
#             paid_amount += p["amount"]
#
#         # 🔴 4. STATUS + DEBT
#         sale.total_amount = total_amount
#         sale.paid_amount = paid_amount
#
#         if paid_amount == total_amount:
#             sale.status = Sale.Status.PAID
#
#         elif paid_amount == 0:
#             sale.status = Sale.Status.DEBT
#
#         elif paid_amount < total_amount:
#             sale.status = Sale.Status.PARTIAL
#
#         else:
#             raise ValidationError("Overpayment not allowed")
#
#         sale.save()
#
#         # 🔴 5. DEBT LOGIC
#         if customer and paid_amount < total_amount:
#             debt_amount = total_amount - paid_amount
#
#             DebtService.increase_debt(
#                 customer=customer,
#                 amount=debt_amount,
#                 sale=sale
#             )
#
#         return sale