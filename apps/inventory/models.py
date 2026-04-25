from django.db import models

from apps.common.models.timestamp_mixin import TimestampMixin
from apps.products.models import Product
from apps.store.models import Store
from apps.users.models import User


# Create your models here.
class InventorySession(TimestampMixin):

    class Status(models.TextChoices):
        ACTIVE = "active"
        COMPLETED = "completed"
        CANCELLED = "cancelled"

    store = models.ForeignKey(Store, on_delete=models.CASCADE, db_index=True)

    started_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    started_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    # snapshot versioning uchun
    snapshot_taken = models.BooleanField(default=False)

    class Meta:
        db_table = "inventory_session"
        indexes = [
            models.Index(fields=["store", "status"]),
        ]


class InventorySnapshot(TimestampMixin):

    session = models.ForeignKey(InventorySession, on_delete=models.CASCADE, related_name="snapshots")

    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    store = models.ForeignKey(Store, on_delete=models.CASCADE)

    expected_quantity = models.IntegerField()  # startdagi stock

    class Meta:
        db_table = "inventory_snapshot"
        unique_together = ("session", "product")
        indexes = [
            models.Index(fields=["session", "product"]),
        ]


class InventoryCount(TimestampMixin):

    class Status(models.TextChoices):
        PENDING = "p", "Pending"
        EQUAL = "e", "Equal"
        LESS = "l", "Less"
        MORE = "m", "More"

    session = models.ForeignKey("InventorySession", on_delete=models.CASCADE, related_name="counts")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)

    counted_quantity = models.IntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    is_check = models.BooleanField(default=False)

    class Meta:
        unique_together = ("session", "product")


class InventoryMovement(TimestampMixin):

    class Type(models.TextChoices):
        SALE = "s", "Sale"
        TRANSFER_OUT = "to", 'Transfer Out'
        RETURN = "r", 'Return'
        TRANSFER_IN = "ti", "Transfer In"
        ENTRY = "e", "Entry"

    session = models.ForeignKey(InventorySession, on_delete=models.CASCADE, related_name="movements")

    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.IntegerField()

    type = models.CharField(max_length=20, choices=Type.choices)

    ref_id = models.IntegerField()  # sale_id yoki transfer_id

    class Meta:
        db_table = "inventory_movement"
        indexes = [
            models.Index(fields=["session", "product"]),
        ]



class InventoryAdjustment(TimestampMixin):

    session = models.ForeignKey(InventorySession, on_delete=models.CASCADE)

    product = models.ForeignKey(Product, on_delete=models.CASCADE)

    difference = models.IntegerField()

    class Meta:
        db_table = "inventory_adjustment"
        indexes = [
            models.Index(fields=["session"]),
        ]
