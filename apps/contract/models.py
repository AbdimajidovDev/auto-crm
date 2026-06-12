from decimal import Decimal

from django.db import models
from django.db.models import Sum

from apps.common.models.timestamp_mixin import TimestampMixin


# Create your models here.

class Supplier(TimestampMixin):

    name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20, db_index=True)
    description = models.TextField()

    inn = models.CharField(max_length=50, unique=True, blank=True, null=True)
    address = models.TextField()

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "supplier"
        verbose_name = "Supplier"
        verbose_name_plural = "Suppliers"

        indexes = [
            models.Index(fields=["phone_number"]),
            models.Index(fields=["inn"]),
        ]

    def __str__(self):
        return f"{self.name}"

    def get_total_debt(self):
        # Jami kirim qilingan qarzlar yig'indisi
        total_in = self.transactions.filter(
            type=SupplierTransaction.TransactionType.INVENTORY_IN
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        # Jami to'langan summalar yig'indisi
        total_paid = self.transactions.filter(
            type=SupplierTransaction.TransactionType.PAYMENT
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        return total_in - total_paid



class StockEntry(TimestampMixin):
    class PaymentType(models.TextChoices):
        Cash = "cash", "Cash"
        Card = "card", "Card"
        Mixed = "mixed", "Aralash"

    supplier = models.ForeignKey(
        "contract.Supplier",
        on_delete=models.PROTECT,
        related_name="entries"
    )

    store = models.ForeignKey(
        "store.Store",
        on_delete=models.PROTECT,
        related_name="entries"
    )
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    cash_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    card_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    paid_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0, editable=False)
    debt_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0, editable=False)
    payment_type = models.CharField(max_length=7, choices=PaymentType.choices, default=PaymentType.Cash, editable=False)

    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True
    )

    class Meta:
        db_table = "stock_entry"
        ordering = ["-created_at"]

    def calculate_payment_fields(self):
        """
        cash_amount va card_amount asosida
        paid_amount, payment_type va debt_amount ni hisoblaydi.
        """
        self.paid_amount = self.cash_amount + self.card_amount

        if self.card_amount > 0 and self.cash_amount <= 0:
            self.payment_type = self.PaymentType.Card
        elif self.cash_amount > 0 and self.card_amount <= 0:
            self.payment_type = self.PaymentType.Cash
        elif self.cash_amount > 0 and self.card_amount > 0:
            self.payment_type = self.PaymentType.Mixed
        else:
            # ikkalasi ham 0 bo'lsa — to'liq qarzga olingan, default Cash
            self.payment_type = self.PaymentType.Cash

        self.debt_amount = self.total_amount - self.paid_amount

    def save(self, *args, **kwargs):
        self.calculate_payment_fields()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"#{self.pk}. {self.supplier} - {self.store}"


class StockEntryItem(models.Model):
    entry = models.ForeignKey(
        StockEntry,
        on_delete=models.CASCADE,
        related_name="items"
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT
    )
    quantity = models.PositiveIntegerField()
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    wholesale_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        db_table = "stock_entry_item"

    def __str__(self):
        return f"{self.entry.supplier.name} - {self.product.name}"



class SupplierTransaction(TimestampMixin):
    class TransactionType(models.TextChoices):
        INVENTORY_IN = "in", "Inventory Intake (Debt Increase)"
        PAYMENT = "pay", "Payment to Supplier (Debt Decrease)"

    supplier = models.ForeignKey("contract.Supplier", on_delete=models.CASCADE, related_name="transactions")
    entry = models.ForeignKey(StockEntry, on_delete=models.SET_NULL, null=True, blank=True)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    type = models.CharField(max_length=5, choices=TransactionType.choices)
    note = models.TextField(blank=True)

    class Meta:
        db_table = "supplier_transaction"
