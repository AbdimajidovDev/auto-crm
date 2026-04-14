from django.db import models
from django.conf import settings

from apps.common.models.timestamp_mixin import TimestampMixin


class Sale(models.Model):

    class Status(models.TextChoices):
        PAID = "paid", "Paid"
        PARTIAL = "partial", "Partial"
        DEBT = "debt", "Debt"

    class DiscountType(models.TextChoices):
        PERCENTAGE = "p", "Percentage (%)"
        FIXED = "f", "Fixed Amount"

    store = models.ForeignKey('store.Store', on_delete=models.CASCADE, db_index=True)
    customer = models.ForeignKey(
        'users.Customer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True
    )
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    total_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    discount_type = models.CharField(
        max_length=10,
        choices=DiscountType.choices,
        null=True,
        blank=True
    )
    discount_value = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0
    )
    discount_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.store.name} {self.customer.full_name} {str(self.status)}"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey('products.Product', on_delete=models.CASCADE)

    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=20, decimal_places=2)
    total_price = models.DecimalField(max_digits=20, decimal_places=2)



class Payment(TimestampMixin):

    class Type(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"

    sale = models.ForeignKey(
        "sales.Sale",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="payments"
    )

    customer = models.ForeignKey(
        "users.Customer",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="payments"
    )

    amount = models.DecimalField(max_digits=20, decimal_places=2)
    type = models.CharField(max_length=5, choices=Type.choices)

    def __str__(self):
        return f"{self.sale.store.name} {self.customer.full_name} {str(self.amount)}"
    