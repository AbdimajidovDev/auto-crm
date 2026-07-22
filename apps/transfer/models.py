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



class TransferSession(TimestampMixin):
    """
    O'tkazma qoralamasi (sessiya): forma to'ldirilayotganda avto-saqlanadi.
    Yuborilmaguncha IN_PROGRESS holatda qoladi — omborga TA'SIR QILMAYDI.
    Yuborilganda haqiqiy StockTransfer yaratiladi va sessiya COMPLETED bo'ladi.
    items JSON ko'rinishida saqlanadi (qoralama — yuborishda qayta validatsiya qilinadi).
    """

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "Jarayonda"
        COMPLETED = "completed", "Yakunlangan"
        CANCELLED = "cancelled", "Bekor qilingan"

    from_store = models.ForeignKey(
        "store.Store",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="transfer_sessions_from",
    )
    to_store = models.ForeignKey(
        "store.Store",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="transfer_sessions_to",
    )

    # [{product, quantity}, ...]
    items = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
        db_index=True,
    )
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="transfer_sessions",
    )

    # Yuborilgandan keyin yaratilgan haqiqiy o'tkazma
    transfer = models.OneToOneField(
        StockTransfer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="session",
    )

    class Meta:
        db_table = "transfer_session"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["created_by", "status"]),
        ]

    def __str__(self):
        return f"TransferSession #{self.pk} [{self.status}]"


class Notification(TimestampMixin):

    class Type(models.TextChoices):
        TRANSFER_CREATED = "tc", "Transfer Created"
        TRANSFER_APPROVED = "ta", "Transfer Approved"
        TRANSFER_REJECTED = "tr", "Transfer Rejected"
        LOW_STOCK_PURCHASE = "lp", "Low Stock - Purchase Required"
        LOW_STOCK_TRANSFER = "lt", "Low Stock - Transfer Required"

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="notifications"
    )

    type = models.CharField(max_length=20, choices=Type.choices)

    title = models.CharField(max_length=255)
    message = models.TextField()

    is_read = models.BooleanField(default=False)

    # 🔗 optional reference
    transfer = models.ForeignKey(
        "transfer.StockTransfer",
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    class Meta:
        db_table = "notification"
        indexes = [
            models.Index(fields=["user", "is_read"]),
        ]