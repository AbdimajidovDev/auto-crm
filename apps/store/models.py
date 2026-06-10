from django.db import models

from apps.common.models.timestamp_mixin import TimestampMixin


# Create your models here.


class Store(TimestampMixin):

    class StoreType(models.TextChoices):
        BASE = "b", "Base"
        STORE = "s", "Store"

    name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20, db_index=True)
    address = models.TextField()

    type = models.CharField(max_length=10, choices=StoreType.choices)

    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "store"
        verbose_name = "Store"
        verbose_name_plural = "Stores"

        indexes = [
            models.Index(fields=["type"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.name}"


class StoreUser(TimestampMixin):

    class Role(models.TextChoices):
        Manager = "m", "Manager"
        SELLER = "s", "Seller"

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="store_links")
    store = models.ForeignKey("store.Store", on_delete=models.CASCADE, related_name="user_links")

    role = models.CharField(max_length=20, choices=Role.choices)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "store_user"
        # verbose_name = "Store User"
        unique_together = ("user", "store")

        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["store"]),
        ]
