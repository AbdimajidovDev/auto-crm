from django.db import models
from django.db.models import Q

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
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=["store", "status"]),
        ]


class InventorySnapshot(TimestampMixin):

    session = models.ForeignKey(InventorySession, on_delete=models.CASCADE, related_name="snapshots")

    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    store = models.ForeignKey(Store, on_delete=models.CASCADE)

    # Decimal: juft mahsulot qoldig'i kasr (0.5 qadam) bo'lishi mumkin
    expected_quantity = models.DecimalField(max_digits=12, decimal_places=2)  # startdagi stock

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

    counted_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    is_check = models.BooleanField(default=False)
    # Javon AYNAN qachon sanalgani. finalize() faqat shu vaqtdan KEYINGI
    # harakatlarni qoldiqqa qo'llaydi — undan oldingilari sanoqda allaqachon
    # aks etgan. `updated_at` (auto_now) bunga yaramaydi: `save(update_fields=...)`
    # ro'yxatga kirmagan auto_now maydonini bazaga yozmaydi.
    counted_at = models.DateTimeField(null=True, blank=True)

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
    quantity = models.DecimalField(max_digits=12, decimal_places=2)

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

    difference = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = "inventory_adjustment"
        indexes = [
            models.Index(fields=["session"]),
        ]


class StockAdjustment(TimestampMixin):
    """
    Qo'lda qilingan qoldiq o'zgarishlari (Import va Hisobdan chiqarish) audit jurnali.

    Qoidalar:
      - IMPORT: Mahsulot qoldig'i qo'lda oshirilganda (+);
      - WRITE_OFF: Mahsulot qoldig'i qo'lda kamaytirilganda (-);
      - RECOUNT: Qoldiq to'g'ridan-to'g'ri yangi qiymatga o'rnatilganda (legacy/inventarizatsiyasiz).
      - Snapshot narxlar: operatsiya vaqtidagi purchase_price va sale_price saqlanadi.
      - Bekor qilish: o'chirilmaydi, status=CANCELLED, cancelled_by, cancelled_at yoziladi va stock teskari o'zgartiriladi.
    """

    class Type(models.TextChoices):
        IMPORT = "import", "Import"
        WRITE_OFF = "write_off", "Hisobdan chiqarish"
        RECOUNT = "recount", "Qayta sanash"

    class Reason(models.TextChoices):
        MANUAL_IMPORT = "manual_import", "Qo'lda kirim / Import"
        RECOUNT = "recount", "Qayta sanash"
        DATA_ERROR = "data_error", "Xato kiritilgan ma'lumot"
        DAMAGED = "damaged", "Buzilgan / yaroqsiz"
        EXPIRED = "expired", "Muddati o'tgan"
        LOST = "lost", "Yo'qolgan / o'g'irlangan"
        FOUND = "found", "Topilgan tovar"
        OTHER = "other", "Boshqa"

    class Status(models.TextChoices):
        ACTIVE = "active", "Faol"
        CANCELLED = "cancelled", "Bekor qilingan"

    type = models.CharField(
        max_length=20, choices=Type.choices, default=Type.IMPORT, db_index=True
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )

    store = models.ForeignKey(
        Store, on_delete=models.PROTECT, related_name="stock_adjustments"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="stock_adjustments"
    )

    # Qo'shilgan yoki ayrilgan sof miqdor
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Qoldiq o'zgarishi
    old_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    new_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    difference = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Operatsiya vaqtidagi narxlar snapshot'i
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    reason = models.CharField(
        max_length=20, choices=Reason.choices, default=Reason.MANUAL_IMPORT, db_index=True
    )
    comment = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_adjustments",
    )

    cancelled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_stock_adjustments",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "stock_adjustment"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["store", "product"]),
            models.Index(fields=["type", "status"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"Adjustment #{self.id} [{self.type}/{self.status}] {self.product_id}: qty={self.quantity}"


class LowStockItem(TimestampMixin):
    """
    Historical low-stock event / replenishment requirement.

    Lifecycle:
      OPEN     -> stock <= min_stock, replenishment required
      RESOLVED -> stock recovered above min_stock

    Only ONE OPEN record may exist per (store, product) — enforced by a
    partial unique constraint at the DB level (see Meta.constraints).
    """

    class ActionType(models.TextChoices):
        PURCHASE = "purchase", "Purchase (supplier)"   # store.type == BASE
        TRANSFER = "transfer", "Transfer (from base)"  # store.type == STORE

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="low_stock_items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="low_stock_items")

    current_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    min_stock = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    action_type = models.CharField(max_length=10, choices=ActionType.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "low_stock_item"
        ordering = ["-created_at"]
        constraints = [
            # Guarantees at most one OPEN record per (store, product) even under
            # concurrent requests — application checks are best-effort only.
            models.UniqueConstraint(
                fields=["store", "product"],
                condition=Q(status="open"),
                name="uniq_open_low_stock_per_store_product",
            ),
        ]
        indexes = [
            # List API filters by status (+ action_type/store/product) and orders by -created_at.
            models.Index(fields=["status", "action_type"]),
            models.Index(fields=["store", "status"]),
            models.Index(fields=["product", "status"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"LowStock {self.store_id}/{self.product_id} [{self.status}]"
