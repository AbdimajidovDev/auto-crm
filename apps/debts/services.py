from django.db import transaction, models
from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from apps.debts.models import CustomerDebt
from apps.sales.models import Payment
from apps.users.models.customers import Customer


class DebtService:

    @staticmethod
    def increase_debt(*, customer, amount, sale=None):
        return CustomerDebt.objects.create(
            customer=customer,
            amount=amount,
            type=CustomerDebt.Type.INCREASE,
            sale=sale
        )

    @staticmethod
    def decrease_debt(*, customer, amount, sale=None):
        return CustomerDebt.objects.create(
            customer=customer,
            amount=amount,
            type=CustomerDebt.Type.DECREASE,
            sale=sale
        )

    @staticmethod
    def get_customer_balance(customer):
        increases = CustomerDebt.objects.filter(
            customer=customer,
            type=CustomerDebt.Type.INCREASE
        ).aggregate(total=Sum("amount"))["total"] or 0

        decreases = CustomerDebt.objects.filter(
            customer=customer,
            type=CustomerDebt.Type.DECREASE
        ).aggregate(total=Sum("amount"))["total"] or 0

        return increases - decreases

    @staticmethod
    @transaction.atomic
    def pay_debt(*, customer_id, amount, payment_type):

        # 🔴 LOCK CUSTOMER (race condition fix)
        customer = Customer.objects.select_for_update().get(id=customer_id)

        if amount <= 0:
            raise ValidationError("Miqdor ijobiy bo'lishi kerak")

        current_debt = DebtService.get_customer_balance(customer)

        if current_debt <= 0:
            raise ValidationError("Mijozning qarzi yo'q")

        if amount > current_debt:
            raise ValidationError("Miqdori qarzdan oshib ketadi ")

        # 🔴 PAYMENT
        payment = Payment.objects.create(
            customer=customer,
            amount=amount,
            type=payment_type
        )

        # 🔴 DEBT REDUCE
        CustomerDebt.objects.create(
            customer=customer,
            amount=amount,
            type=CustomerDebt.Type.DECREASE
        )

        return payment