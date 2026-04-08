from django.db import models

from apps.common.models.timestamp_mixin import TimestampMixin


# Create your models here.

class Supplier(TimestampMixin):

    name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20, db_index=True)
    description = models.TextField()

    inn = models.CharField(max_length=50, unique=True)
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
        return self.name



class StockEntry(TimestampMixin):
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

    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True
    )

    class Meta:
        db_table = "stock_entry"
        ordering = ["-created_at"]

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

    class Meta:
        db_table = "stock_entry_item"

    def __str__(self):
        return f"{self.entry.supplier.name} - {self.product.name}"
