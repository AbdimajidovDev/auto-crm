from decimal import Decimal

from django.core.exceptions import ValidationError
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


class StockLot(TimestampMixin):
    """
    Mualliflik (partiya) darajasidagi qoldiq modeli — Authoritative lot-level stock state.

    Har bir qabul qilingan tovar partiyasi (xarid, filiallararo kirim,
    inventarizatsiya ortiqchaligi, cut-off ochilish qoldig'i) uchun alohida yozuv.
    FIFO tartibida kamaytiriladi (remaining_quantity).
    """

    class LotType(models.TextChoices):
        PURCHASE = "purchase", "Purchase"
        TRANSFER_IN = "transfer_in", "Transfer In"
        INVENTORY_EXCESS = "inventory_excess", "Inventory Excess"
        OPENING_BALANCE = "opening_balance", "Opening Balance"

    store = models.ForeignKey(
        Store,
        on_delete=models.PROTECT,
        related_name="stock_lots",
        db_index=True,
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="stock_lots",
        db_index=True,
    )
    supplier = models.ForeignKey(
        "contract.Supplier",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_lots",
        db_index=True,
    )
    lot_type = models.CharField(
        max_length=30,
        choices=LotType.choices,
        default=LotType.PURCHASE,
        db_index=True,
    )
    source_lot = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="derived_lots",
    )
    stock_entry_item = models.ForeignKey(
        "contract.StockEntryItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_lots",
    )

    initial_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    remaining_quantity = models.DecimalField(max_digits=12, decimal_places=2, db_index=True)
    purchase_price = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "stock_lot"
        ordering = ["created_at", "id"]
        constraints = [
            # initial_quantity > 0
            models.CheckConstraint(
                check=Q(initial_quantity__gt=0),
                name="stock_lot_initial_qty_gt_zero",
            ),
            # remaining_quantity >= 0
            models.CheckConstraint(
                check=Q(remaining_quantity__gte=0),
                name="stock_lot_remaining_qty_gte_zero",
            ),
            # TRANSFER_IN lot must specify a source_lot
            models.CheckConstraint(
                check=~Q(lot_type="transfer_in") | Q(source_lot__isnull=False),
                name="stock_lot_transfer_in_has_source",
            ),
            # source_lot must not point to itself
            models.CheckConstraint(
                check=Q(source_lot__isnull=True) | ~Q(source_lot=models.F("id")),
                name="stock_lot_prevent_self_reference",
            ),
        ]
        indexes = [
            models.Index(
                fields=["store", "product", "remaining_quantity", "created_at"],
                name="stock_lot_store_prod_rem_idx",
            ),
            models.Index(
                fields=["supplier", "product"],
                name="stock_lot_supplier_prod_idx",
            ),
            # Partial index optimized for FIFO deduction queries
            models.Index(
                fields=["store", "product", "created_at"],
                condition=Q(remaining_quantity__gt=0),
                name="stock_lot_fifo_partial_idx",
            ),
            models.Index(
                fields=["created_at"],
                name="stock_lot_created_at_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.initial_quantity is not None and self.initial_quantity <= Decimal("0"):
            raise ValidationError({"initial_quantity": "Initial quantity must be strictly greater than zero."})
        if self.remaining_quantity is not None and self.remaining_quantity < Decimal("0"):
            raise ValidationError({"remaining_quantity": "Remaining quantity cannot be negative."})
        if self.source_lot_id and self.pk and self.source_lot_id == self.pk:
            raise ValidationError({"source_lot": "A lot cannot reference itself as source_lot."})
        if self.source_lot_id and self.source_lot:
            if self.product_id and self.source_lot.product_id != self.product_id:
                raise ValidationError({"source_lot": "Source lot must have the same product."})
            if self.lot_type == self.LotType.TRANSFER_IN and self.store_id and self.source_lot.store_id == self.store_id:
                raise ValidationError({"source_lot": "TRANSFER_IN source lot must belong to a different store."})
        if self.lot_type == self.LotType.TRANSFER_IN and not self.source_lot_id:
            raise ValidationError({"source_lot": "TRANSFER_IN lot must specify a source_lot."})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Lot #{self.pk} [{self.lot_type}] Store:{self.store_id} Prod:{self.product_id} Rem:{self.remaining_quantity}/{self.initial_quantity}"


class StockAllocation(TimestampMixin):
    """
    O'zgarmas inventar harakatlari daftari — Immutable inventory movement ledger.

    Har bir tovar kamayishi yoki qaytishi qaysi StockLot'dan olinganini yoki
    qaysi StockLot'ga qaytarilganini yozib boradi.
    Tarix hech qachon o'zgartirilmaydi yoki o'chirilmaydi.
    """

    class MovementType(models.TextChoices):
        SALE = "sale", "Sale"
        SALE_RETURN = "sale_return", "Sale Return"
        WRITE_OFF = "write_off", "Write Off"
        TRANSFER_OUT = "transfer_out", "Transfer Out"
        TRANSFER_IN = "transfer_in", "Transfer In"
        INVENTORY_SHORTAGE = "inventory_shortage", "Inventory Shortage"
        INVENTORY_EXCESS = "inventory_excess", "Inventory Excess"
        SUPPLIER_RETURN = "supplier_return", "Supplier Return"

    class Direction(models.TextChoices):
        IN = "in", "In"
        OUT = "out", "Out"

    lot = models.ForeignKey(
        StockLot,
        on_delete=models.PROTECT,
        related_name="allocations",
        db_index=True,
    )
    movement_type = models.CharField(
        max_length=30,
        choices=MovementType.choices,
        db_index=True,
    )
    direction = models.CharField(
        max_length=5,
        choices=Direction.choices,
        db_index=True,
    )

    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))

    # Nullable source references (Document line items)
    sale_item = models.ForeignKey(
        "sales.SaleItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_allocations",
    )
    sale_return_item = models.ForeignKey(
        "sales.SaleReturnItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_allocations",
    )
    write_off_item = models.ForeignKey(
        "writeoff.WriteOffItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_allocations",
    )
    transfer_item = models.ForeignKey(
        "transfer.StockTransferItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_allocations",
    )
    supplier_return_item = models.ForeignKey(
        "contract.StockEntryReturnItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_allocations",
    )

    reversal_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversals",
        db_index=True,
        help_text="Ushbu allocation bekor qilayotgan asl OUT allocation.",
    )

    inventory_session = models.ForeignKey(
        "inventory.InventorySession",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_allocations",
        db_index=True,
    )

    # Tizimli / inventarizatsiya harakatlari uchun tavsif / izoh
    description = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "stock_allocation"
        ordering = ["created_at", "id"]
        constraints = [
            # quantity > 0
            models.CheckConstraint(
                check=Q(quantity__gt=0),
                name="stock_allocation_qty_gt_zero",
            ),
            # Movement type & Direction invariants
            models.CheckConstraint(
                check=(
                    (
                        Q(movement_type__in=[
                            "sale",
                            "write_off",
                            "transfer_out",
                            "inventory_shortage",
                            "supplier_return",
                        ])
                        & Q(direction="out")
                    )
                    | (
                        Q(movement_type__in=[
                            "sale_return",
                            "transfer_in",
                            "inventory_excess",
                        ])
                        & Q(direction="in")
                    )
                ),
                name="stock_allocation_movement_direction_match",
            ),
            # Source Exclusivity Invariant:
            # Har bir operatsion harakat o'ziga mos yagona source FK'ga ega bo'lishi shart.
            # Inventarizatsiya harakatlarida (shortage/excess) barcha item FKlar NULL bo'ladi.
            models.CheckConstraint(
                check=(
                    (
                        Q(movement_type="sale")
                        & Q(sale_item__isnull=False)
                        & Q(sale_return_item__isnull=True)
                        & Q(write_off_item__isnull=True)
                        & Q(transfer_item__isnull=True)
                        & Q(supplier_return_item__isnull=True)
                    )
                    | (
                        Q(movement_type="sale_return")
                        & Q(sale_item__isnull=True)
                        & Q(sale_return_item__isnull=False)
                        & Q(write_off_item__isnull=True)
                        & Q(transfer_item__isnull=True)
                        & Q(supplier_return_item__isnull=True)
                    )
                    | (
                        Q(movement_type="write_off")
                        & Q(sale_item__isnull=True)
                        & Q(sale_return_item__isnull=True)
                        & Q(write_off_item__isnull=False)
                        & Q(transfer_item__isnull=True)
                        & Q(supplier_return_item__isnull=True)
                    )
                    | (
                        Q(movement_type__in=["transfer_out", "transfer_in"])
                        & Q(sale_item__isnull=True)
                        & Q(sale_return_item__isnull=True)
                        & Q(write_off_item__isnull=True)
                        & Q(transfer_item__isnull=False)
                        & Q(supplier_return_item__isnull=True)
                    )
                    | (
                        Q(movement_type="supplier_return")
                        & Q(sale_item__isnull=True)
                        & Q(sale_return_item__isnull=True)
                        & Q(write_off_item__isnull=True)
                        & Q(transfer_item__isnull=True)
                        & Q(supplier_return_item__isnull=False)
                    )
                    | (
                        Q(movement_type__in=["inventory_shortage", "inventory_excess"])
                        & Q(sale_item__isnull=True)
                        & Q(sale_return_item__isnull=True)
                        & Q(write_off_item__isnull=True)
                        & Q(transfer_item__isnull=True)
                        & Q(supplier_return_item__isnull=True)
                    )
                ),
                name="stock_allocation_source_exclusivity",
            ),
            # reversal_of faqat SALE_RETURN uchun ruxsat etiladi
            models.CheckConstraint(
                check=Q(movement_type="sale_return") | Q(reversal_of__isnull=True),
                name="stock_alloc_reversal_only_sale_return",
            ),
            # reversal_of o'z-o'ziga reference bo'la olmaydi
            models.CheckConstraint(
                check=Q(reversal_of__isnull=True) | ~Q(reversal_of=models.F("id")),
                name="stock_alloc_prevent_self_reversal",
            ),
            # inventory_session faqat va majburiy INVENTORY_SHORTAGE / INVENTORY_EXCESS uchun
            models.CheckConstraint(
                check=(
                    (
                        Q(movement_type__in=["inventory_shortage", "inventory_excess"])
                        & Q(inventory_session__isnull=False)
                    )
                    | (
                        ~Q(movement_type__in=["inventory_shortage", "inventory_excess"])
                        & Q(inventory_session__isnull=True)
                    )
                ),
                name="stock_alloc_inventory_session_match",
            ),
        ]
        indexes = [
            models.Index(fields=["lot", "created_at"], name="stock_alloc_lot_created_idx"),
            models.Index(fields=["movement_type", "created_at"], name="stock_alloc_mov_type_idx"),
            models.Index(fields=["sale_item"], name="stock_alloc_sale_item_idx"),
            models.Index(fields=["sale_return_item"], name="stock_alloc_return_item_idx"),
            models.Index(fields=["write_off_item"], name="stock_alloc_woff_item_idx"),
            models.Index(fields=["transfer_item"], name="stock_alloc_trans_item_idx"),
            models.Index(fields=["supplier_return_item"], name="stock_alloc_sup_ret_idx"),
            models.Index(fields=["movement_type", "created_at", "lot"], name="stock_alloc_rep_idx"),
        ]

    def clean(self):
        super().clean()
        if self.quantity is not None and self.quantity <= Decimal("0"):
            raise ValidationError({"quantity": "Quantity must be strictly greater than zero."})

        # Direction checks
        out_types = {
            self.MovementType.SALE,
            self.MovementType.WRITE_OFF,
            self.MovementType.TRANSFER_OUT,
            self.MovementType.INVENTORY_SHORTAGE,
            self.MovementType.SUPPLIER_RETURN,
        }
        in_types = {
            self.MovementType.SALE_RETURN,
            self.MovementType.TRANSFER_IN,
            self.MovementType.INVENTORY_EXCESS,
        }
        if self.movement_type in out_types and self.direction != self.Direction.OUT:
            raise ValidationError({"direction": f"Movement type '{self.movement_type}' requires direction='out'."})
        if self.movement_type in in_types and self.direction != self.Direction.IN:
            raise ValidationError({"direction": f"Movement type '{self.movement_type}' requires direction='in'."})

        # Source exclusivity checks
        item_fields = {
            "sale_item": self.sale_item_id,
            "sale_return_item": self.sale_return_item_id,
            "write_off_item": self.write_off_item_id,
            "transfer_item": self.transfer_item_id,
            "supplier_return_item": self.supplier_return_item_id,
        }
        active_sources = [k for k, v in item_fields.items() if v is not None]

        if self.movement_type == self.MovementType.SALE:
            if self.sale_item_id is None or len(active_sources) != 1:
                raise ValidationError({"sale_item": "SALE movement strictly requires sale_item and no other source."})
        elif self.movement_type == self.MovementType.SALE_RETURN:
            if self.sale_return_item_id is None or len(active_sources) != 1:
                raise ValidationError({"sale_return_item": "SALE_RETURN movement strictly requires sale_return_item and no other source."})
        elif self.movement_type == self.MovementType.WRITE_OFF:
            if self.write_off_item_id is None or len(active_sources) != 1:
                raise ValidationError({"write_off_item": "WRITE_OFF movement strictly requires write_off_item and no other source."})
        elif self.movement_type in (self.MovementType.TRANSFER_OUT, self.MovementType.TRANSFER_IN):
            if self.transfer_item_id is None or len(active_sources) != 1:
                raise ValidationError({"transfer_item": f"{self.movement_type} movement strictly requires transfer_item and no other source."})
        elif self.movement_type == self.MovementType.SUPPLIER_RETURN:
            if self.supplier_return_item_id is None or len(active_sources) != 1:
                raise ValidationError({"supplier_return_item": "SUPPLIER_RETURN movement strictly requires supplier_return_item and no other source."})
        elif self.movement_type in (self.MovementType.INVENTORY_SHORTAGE, self.MovementType.INVENTORY_EXCESS):
            if len(active_sources) > 0:
                raise ValidationError("Inventory shortage/excess must not have domain item foreign keys.")

        # reversal_of validation
        if self.reversal_of_id is not None:
            if self.movement_type != self.MovementType.SALE_RETURN:
                raise ValidationError({"reversal_of": "reversal_of can only be set on SALE_RETURN allocations."})
            if self.pk and self.reversal_of_id == self.pk:
                raise ValidationError({"reversal_of": "An allocation cannot reverse itself."})
            if self.reversal_of:
                if self.reversal_of.direction != self.Direction.OUT:
                    raise ValidationError({"reversal_of": "reversal_of must point to an OUT allocation."})
                if self.reversal_of.movement_type == self.MovementType.SALE_RETURN:
                    raise ValidationError({"reversal_of": "reversal_of cannot point to another SALE_RETURN."})
                if self.reversal_of.lot_id != self.lot_id:
                    raise ValidationError({"reversal_of": "reversal_of must belong to the same StockLot."})

        # inventory_session validation
        if self.movement_type in (self.MovementType.INVENTORY_SHORTAGE, self.MovementType.INVENTORY_EXCESS):
            if self.inventory_session_id is None:
                raise ValidationError({"inventory_session": f"{self.movement_type} strictly requires inventory_session."})
        else:
            if self.inventory_session_id is not None:
                raise ValidationError({"inventory_session": f"inventory_session must be NULL for {self.movement_type}."})

    def save(self, *args, **kwargs):
        self.clean()
        if self.pk is not None and not kwargs.get("force_insert", False):
            raise ValidationError("StockAllocation is an immutable audit ledger and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("StockAllocation records cannot be deleted.")

    def __str__(self):
        return f"Allocation #{self.pk} [{self.movement_type}/{self.direction}] Lot:{self.lot_id} Qty:{self.quantity} Cost:{self.unit_cost}"
