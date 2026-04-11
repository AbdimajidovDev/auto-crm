from django.db import models
from django.conf import settings

from apps.common.models.timestamp_mixin import TimestampMixin


class CustomerDebt(TimestampMixin):

    class Type(models.TextChoices):
        INCREASE = "i", "Increase"
        DECREASE = "d", "Decrease"

    customer = models.ForeignKey(
        'users.Customer',
        on_delete=models.CASCADE,
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

    def __str__(self):
        return f"{self.customer} | {self.amount} | {self.type}"
