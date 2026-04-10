from django.db import models

from apps.common.models.timestamp_mixin import TimestampMixin


# Create your models here.



class StockTransfer(TimestampMixin):

    class Status(models.TextChoices):
        PENDING = "p", "Pending"
        APPROVED = "a", "Approved"
        REJECTED = "r", "Rejected"

    from_store = models.ForeignKey("store.Store", on_delete=models.CASCADE,related_name="outgoing_transfers")
    to_store = models.ForeignKey("store.Store", on_delete=models.CASCADE, related_name="incoming_transfers")
    status = models.CharField(max_length=1, choices=Status.choices, default=Status.PENDING)
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_transfers"
    )

    approved_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_transfers"
    )

    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "stock_transfer"
        ordering = ["-created_at"]


class StockTransferItem(models.Model):
    stock_transfer = models.ForeignKey("StockTransfer", on_delete=models.CASCADE,related_name="items")
    product = models.ForeignKey("products.Product", on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stock_transfer_item"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product.name} {self.quantity}."
