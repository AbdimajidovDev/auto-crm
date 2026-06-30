from django.db import models

from apps.common.models.timestamp_mixin import TimestampMixin


# Create your models here.


class WriteOff(TimestampMixin):
    """
    Mahsulotni hisobdan chiqarish (spisaniye / write-off).

    Mahsulot sotilmasdan yoki transfer qilinmasdan qoldiqdan kamaytiriladi:
    buzilgan, muddati o'tgan, yo'qolgan, katalogdan chiqarilgan yoki
    inventarizatsiyada kam chiqqan (kamomad) tovarlar uchun.

    Stock (ProductBatch.quantity) WriteOffService ichida kamaytiriladi —
    bitta WriteOff bir nechta WriteOffItem dan iborat bo'lishi mumkin.
    """

    class Reason(models.TextChoices):
        DAMAGED = "damaged", "Buzilgan / yaroqsiz"
        EXPIRED = "expired", "Muddati o'tgan"
        LOST = "lost", "Yo'qolgan / o'g'irlangan"
        INVENTORY = "inventory", "Inventarizatsiya kamomadi"
        CATALOG = "catalog", "Katalogdan chiqarish"
        OTHER = "other", "Boshqa"

    store = models.ForeignKey(
        "store.Store",
        on_delete=models.PROTECT,
        related_name="write_offs",
    )

    reason = models.CharField(
        max_length=20,
        choices=Reason.choices,
        default=Reason.OTHER,
        db_index=True,
    )
    comment = models.TextField(blank=True, default="")

    # items bo'yicha tannarx yig'indisi (zarar summasi) — service ichida hisoblanadi.
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="write_offs",
    )

    # Inventarizatsiyadan avtomatik yaratilgan bo'lsa — qaysi sessiyadan ekani.
    inventory_session = models.ForeignKey(
        "inventory.InventorySession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="write_offs",
    )

    class Meta:
        db_table = "write_off"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["store", "reason"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"#{self.pk} {self.store.name} [{self.reason}]"


class WriteOffItem(models.Model):
    write_off = models.ForeignKey(
        WriteOff,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
    )

    quantity = models.PositiveIntegerField()

    # Hisobdan chiqarilgan paytdagi narxlar (batchdan olinadi) — tarix uchun saqlanadi.
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = "write_off_item"
        indexes = [
            models.Index(fields=["write_off"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self):
        return f"{self.product.name} x{self.quantity}"






"""
Excel orqali kiritilgan malumotlarni dbdan o'chirish:


BEGIN;

TRUNCATE TABLE
    write_off_item,
    write_off,

    sale_return_item,
    sale_return,

    sales_payment,
    sales_saleitem,
    sales_sale,

    stock_transfer_item,
    stock_transfer,

    stock_entry_item,
    stock_entry,

    supplier_transaction,

    inventory_snapshot,
    inventory_adjustment,
    inventory_movement,
    inventory_inventorycount,
    inventory_session,
    low_stock_item,

    product_image,
    product_batch,
    product,

    supplier,
    category,
    brand,
    product_unit_measurement,
    product_location

RESTART IDENTITY CASCADE;

COMMIT;
"""