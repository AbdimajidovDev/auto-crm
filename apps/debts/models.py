from django.db import models
from django.conf import settings

from apps.common.models.timestamp_mixin import TimestampMixin


class CustomerDebt(TimestampMixin):

    class Type(models.TextChoices):
        INCREASE = "i", "Increase"
        DECREASE = "d", "Decrease"

    # PROTECT — qarzi bor mijozni o'chirib bo'lmaydi. Ilgari CASCADE edi:
    # mijoz o'chirilsa qarz daftari va to'lovlar yo'qolar, `Sale` esa
    # (SET_NULL bo'lgani uchun) status='debt' bilan qolib ketardi — ya'ni
    # debitorlik jimgina nolga tushardi.
    customer = models.ForeignKey(
        'users.Customer',
        on_delete=models.PROTECT,
        related_name='debts'
    )
    sale = models.ForeignKey(
        'sales.Sale',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='debt_records'
    )
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    type = models.CharField(max_length=2, choices=Type.choices)
    due_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.customer} | {self.amount} | {self.type}"
