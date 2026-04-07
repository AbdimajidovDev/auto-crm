from django.db import models

from apps.common.models.timestamp_mixin import TimestampMixin


# Create your models here.



class StockTransfer(TimestampMixin):

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    from_store = models.ForeignKey(
        "store.Store",
        on_delete=models.CASCADE,
        related_name="outgoing_transfers"
    )
    to_store = models.ForeignKey(
        "store.Store",
        on_delete=models.CASCADE,
        related_name="incoming_transfers"
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE
    )

    quantity = models.PositiveIntegerField()

    # 🔥 narx snapshot (transfer paytidagi)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )

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


# class StockTransfer(TimestampMixin):
#     from_store = models.ForeignKey("store.Store", on_delete=models.CASCADE, related_name="outgoing")
#     to_store = models.ForeignKey("store.Store", on_delete=models.CASCADE, related_name="incoming")
#
#     product = models.ForeignKey("products.Product", on_delete=models.CASCADE)
#
#     quantity = models.IntegerField()
#
#     purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
#     selling_price = models.DecimalField(max_digits=12, decimal_places=2)
#
#     barcode = models.CharField(max_length=12)
#
#     class Meta:
#         db_table = "stock_transfer"
