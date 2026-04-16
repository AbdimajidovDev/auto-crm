from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.debts.models import CustomerDebt
from apps.sales.models import Sale, Payment


class DebtService:

    @staticmethod
    def get_sale_debt(sale):
        increases = CustomerDebt.objects.filter(
            sale=sale,
            type=CustomerDebt.Type.INCREASE
        ).aggregate(total=Sum("amount"))["total"] or 0

        decreases = CustomerDebt.objects.filter(
            sale=sale,
            type=CustomerDebt.Type.DECREASE
        ).aggregate(total=Sum("amount"))["total"] or 0

        return increases - decreases

    @staticmethod
    @transaction.atomic
    def pay_debt(*, sale_id, amount, payment_type):

        # 🔴 LOCK SALE (critical!)
        # sale = Sale.objects.select_for_update().select_related("customer").get(id=sale_id)
        sale = Sale.objects.select_for_update().get(id=sale_id)
        # customer = sale.customer

        # if not customer:
        #     raise ValidationError("Sale mijozga bog'lanmagan")

        if amount <= 0:
            raise ValidationError("Miqdor ijobiy bo'lishi kerak")

        current_debt = DebtService.get_sale_debt(sale)

        if current_debt <= 0:
            raise ValidationError("Bu sotuvda qarz yo'q")

        if amount > current_debt:
            raise ValidationError("Miqdor qarzdan oshib ketdi")

        # 🔴 PAYMENT
        payment = Payment.objects.create(
            customer=sale.customer,
            amount=amount,
            type=payment_type,
            sale=sale  # 🔥 MUHIM
        )

        # 🔴 DEBT REDUCE (SALE BILAN)
        CustomerDebt.objects.create(
            customer=sale.customer,
            sale=sale,
            amount=amount,
            type=CustomerDebt.Type.DECREASE
        )

        return payment

    @staticmethod
    @transaction.atomic
    def increase_debt(*, customer, sale, amount):

        if not customer:
            raise ValidationError("Customer bo‘lishi kerak")

        if amount <= 0:
            raise ValidationError("Amount > 0 bo‘lishi kerak")

        return CustomerDebt.objects.create(
            customer=customer,
            sale=sale,
            amount=amount,
            type=CustomerDebt.Type.INCREASE
        )
