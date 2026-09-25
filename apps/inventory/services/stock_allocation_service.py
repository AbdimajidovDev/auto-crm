from decimal import Decimal
from typing import NamedTuple

from django.db import models, transaction

from apps.inventory.exceptions import (
    AllocationConflictError,
    InsufficientStockError,
    InvalidAllocationError,
    InvalidReversalError,
)
from apps.inventory.models import InventorySession, StockAllocation, StockLot
from apps.products.models import Product, ProductBatch
from apps.sales.models import SaleItem, SaleReturnItem
from apps.store.models import Store
from apps.transfer.models import StockTransferItem
from apps.writeoff.models import WriteOffItem


class InventoryExcessResult(NamedTuple):
    lot: StockLot
    allocation: StockAllocation


class StockAllocationService:
    """
    Production-ready domain service for Lot/Batch Tracking (FIFO / LIFO / Ledger Engine).

    Authoritative Responsibilities:
      1. Authoritative lot state management on StockLot (remaining_quantity).
      2. Append-only immutable movement ledger creation on StockAllocation.
      3. Synchronized aggregate stock cache maintenance on ProductBatch.
         Invariant: ProductBatch.quantity == SUM(StockLot.remaining_quantity).
      4. Deterministic deadlock-free locking:
         transaction.atomic -> ProductBatch rows locked (sorted by product_id ASC)
         -> StockLot rows locked (ordered by created_at ASC, id ASC).
      5. Strict Reverse LIFO restoration on returns via StockAllocation.reversal_of.
    """

    @classmethod
    def _lock_product_batch(cls, store: Store, product: Product) -> ProductBatch:
        """
        Ensures a ProductBatch row exists and acquires an exclusive row-level lock (SELECT FOR UPDATE).
        """
        batch, _ = ProductBatch.objects.get_or_create(
            store=store,
            product=product,
            defaults={
                "quantity": Decimal("0.00"),
                "purchase_price": getattr(product, "purchase_price", Decimal("0.00")) or Decimal("0.00"),
                "selling_price": getattr(product, "price", Decimal("0.00")) or Decimal("0.00"),
            },
        )
        return ProductBatch.objects.select_for_update().get(pk=batch.pk)

    @classmethod
    def _lock_batches_and_lots(
        cls, store: Store, product_ids: list[int]
    ) -> tuple[dict[int, ProductBatch], dict[int, list[StockLot]]]:
        """
        Deterministic deadlock-free locking for multi-product operations:
        1. Unique product_ids sorted ASC.
        2. Ensure and lock ProductBatch rows in ASC product_id order.
        3. Lock open StockLot rows (remaining_quantity > 0) ordered by created_at ASC, id ASC.
        """
        sorted_ids = sorted(list(set(product_ids)))

        batches_by_product = {}
        for pid in sorted_ids:
            batch, _ = ProductBatch.objects.get_or_create(
                store=store,
                product_id=pid,
                defaults={
                    "quantity": Decimal("0.00"),
                    "purchase_price": Decimal("0.00"),
                    "selling_price": Decimal("0.00"),
                },
            )

        locked_batches = list(
            ProductBatch.objects.select_for_update()
            .filter(store=store, product_id__in=sorted_ids)
            .order_by("product_id")
        )
        for b in locked_batches:
            batches_by_product[b.product_id] = b

        lots_by_product = {pid: [] for pid in sorted_ids}
        locked_lots = list(
            StockLot.objects.select_for_update()
            .filter(store=store, product_id__in=sorted_ids, remaining_quantity__gt=Decimal("0.00"))
            .order_by("created_at", "id")
        )
        for lot in locked_lots:
            lots_by_product[lot.product_id].append(lot)

        return batches_by_product, lots_by_product

    @classmethod
    def _sync_product_batch(
        cls,
        store: Store,
        product: Product,
        sync_prices: bool = False,
    ) -> ProductBatch:
        """
        Synchronizes ProductBatch.quantity to strictly match SUM(StockLot.remaining_quantity).
        Must be executed within an active transaction where ProductBatch is locked.

        Price synchronization rules:
        - If batch does not exist: creates ProductBatch initialized with active lot's prices.
        - If batch exists:
          Only updates prices if:
            1. sync_prices is True (e.g. after transfer_in or when an active lot was exhausted during deduction), or
            2. batch was previously empty (old_batch_quantity <= 0) and now receives positive stock, or
            3. batch prices are uninitialized (purchase_price == 0).
          Otherwise, performs a fast, bounded quantity update without redundant queries.
        """
        total_remaining = (
            StockLot.objects.filter(store=store, product=product).aggregate(
                total=models.Sum("remaining_quantity")
            )["total"]
            or Decimal("0.00")
        )

        batch = ProductBatch.objects.select_for_update().filter(store=store, product=product).first()

        if batch is None:
            active_lot = (
                StockLot.objects.filter(
                    store=store,
                    product=product,
                    remaining_quantity__gt=Decimal("0.00"),
                )
                .select_related("stock_entry_item", "source_lot")
                .order_by("created_at", "id")
                .first()
            )
            if active_lot is None:
                active_lot = (
                    StockLot.objects.filter(store=store, product=product)
                    .select_related("stock_entry_item", "source_lot")
                    .order_by("-created_at", "-id")
                    .first()
                )

            active_purchase = (
                active_lot.purchase_price
                if active_lot
                else (getattr(product, "purchase_price", Decimal("0.00")) or Decimal("0.00"))
            )
            active_selling = (
                active_lot.resolve_selling_price()
                if active_lot
                else (getattr(product, "price", Decimal("0.00")) or Decimal("0.00"))
            )
            active_wholesale = (
                active_lot.resolve_wholesale_price()
                if active_lot
                else Decimal("0.00")
            )

            batch = ProductBatch.objects.create(
                store=store,
                product=product,
                quantity=total_remaining,
                purchase_price=active_purchase,
                selling_price=active_selling,
                wholesale_price=active_wholesale,
            )
        else:
            old_batch_quantity = batch.quantity
            update_fields = ["quantity", "updated_at"]
            batch.quantity = total_remaining

            should_update_prices = (
                sync_prices
                or (old_batch_quantity <= Decimal("0.00") and total_remaining > Decimal("0.00"))
                or (batch.purchase_price == Decimal("0.00") and total_remaining > Decimal("0.00"))
            )

            if should_update_prices:
                active_lot = (
                    StockLot.objects.filter(
                        store=store,
                        product=product,
                        remaining_quantity__gt=Decimal("0.00"),
                    )
                    .select_related("stock_entry_item", "source_lot")
                    .order_by("created_at", "id")
                    .first()
                )
                if active_lot:
                    active_purchase = active_lot.purchase_price
                    active_selling = active_lot.resolve_selling_price()
                    active_wholesale = active_lot.resolve_wholesale_price()

                    if active_purchase is not None and active_purchase > Decimal("0.00"):
                        if batch.purchase_price != active_purchase:
                            batch.purchase_price = active_purchase
                            update_fields.append("purchase_price")
                    elif batch.purchase_price == Decimal("0.00") and active_purchase is not None:
                        batch.purchase_price = active_purchase
                        update_fields.append("purchase_price")

                    # Maintain existing positive current store selling price; only initialize if 0.00
                    if batch.selling_price == Decimal("0.00") and active_selling is not None and active_selling > Decimal("0.00"):
                        batch.selling_price = active_selling
                        update_fields.append("selling_price")

                    if batch.wholesale_price == Decimal("0.00") and active_wholesale is not None and active_wholesale > Decimal("0.00"):
                        batch.wholesale_price = active_wholesale
                        update_fields.append("wholesale_price")

            batch.save(update_fields=list(set(update_fields)))

        return batch

    @classmethod
    def ensure_lot_coverage(cls, store: Store, product: Product) -> None:
        """
        Ensures that if ProductBatch has positive stock, it is backed by authoritative StockLot records.
        If ProductBatch.quantity > SUM(StockLot.remaining_quantity), creates an OPENING_BALANCE
        lot for the difference (without creating movement allocations, satisfying Step 4 cut-off isolation).
        """
        batch = ProductBatch.objects.select_for_update().filter(store=store, product=product).first()
        if not batch or batch.quantity <= Decimal("0.00"):
            return

        total_lot_qty = (
            StockLot.objects.filter(store=store, product=product).aggregate(
                total=models.Sum("remaining_quantity")
            )["total"]
            or Decimal("0.00")
        )

        if batch.quantity > total_lot_qty:
            diff = batch.quantity - total_lot_qty
            StockLot.objects.create(
                store=store,
                product=product,
                supplier=None,
                lot_type=StockLot.LotType.OPENING_BALANCE,
                initial_quantity=diff,
                remaining_quantity=diff,
                purchase_price=batch.purchase_price or Decimal("0.00"),
            )

    @classmethod
    def _deduct_fifo(
        cls,
        *,
        store: Store,
        product: Product,
        quantity: Decimal,
        movement_type: str,
        direction: str = StockAllocation.Direction.OUT,
        item_kwargs: dict | None = None,
        lot_queryset_filter: dict | None = None,
        description: str = "",
    ) -> list[StockAllocation]:
        """
        Internal core FIFO deduction engine:
        - Locks ProductBatch for (store, product).
        - Ensures lot coverage for un-lotted legacy batch quantities.
        - Locks open StockLots (created_at ASC, id ASC).
        - Verifies sufficient stock; raises InsufficientStockError if not enough.
        - Sequentially consumes lot remaining_quantity.
        - Creates immutable StockAllocation records.
        - Synchronizes ProductBatch.quantity.
        """
        if quantity is None or Decimal(str(quantity)) <= Decimal("0.00"):
            raise InvalidAllocationError("Allocation quantity must be strictly greater than zero.")

        req_qty = Decimal(str(quantity))

        # 1. Lock ProductBatch
        cls._lock_product_batch(store, product)

        # 2. Lock open StockLots matching filter
        base_filter = {
            "store": store,
            "product": product,
            "remaining_quantity__gt": Decimal("0.00"),
        }
        if lot_queryset_filter:
            base_filter.update(lot_queryset_filter)

        lots = list(
            StockLot.objects.select_for_update()
            .filter(**base_filter)
            .order_by("created_at", "id")
        )

        total_available = sum((lot.remaining_quantity for lot in lots), Decimal("0.00"))
        if total_available < req_qty:
            # On-demand bridge: check if ProductBatch has un-lotted legacy stock
            cls.ensure_lot_coverage(store, product)
            lots = list(
                StockLot.objects.select_for_update()
                .filter(**base_filter)
                .order_by("created_at", "id")
            )
            total_available = sum((lot.remaining_quantity for lot in lots), Decimal("0.00"))

        if total_available < req_qty:
            raise InsufficientStockError(
                f"Insufficient stock for product '{product.name}' (ID: {product.pk}) "
                f"in store '{store.name}' (ID: {store.pk}). "
                f"Requested: {req_qty}, Available: {total_available}."
            )

        remaining_needed = req_qty
        created_allocations = []

        for lot in lots:
            if remaining_needed <= Decimal("0.00"):
                break

            deduct_qty = min(lot.remaining_quantity, remaining_needed)
            lot.remaining_quantity -= deduct_qty
            lot.save(update_fields=["remaining_quantity"])
            remaining_needed -= deduct_qty

            alloc_kwargs = {
                "lot": lot,
                "movement_type": movement_type,
                "direction": direction,
                "quantity": deduct_qty,
                "unit_cost": lot.purchase_price,
                "description": description,
            }
            if item_kwargs:
                alloc_kwargs.update(item_kwargs)

            alloc = StockAllocation.objects.create(**alloc_kwargs)
            created_allocations.append(alloc)

        # 3. Synchronize ProductBatch aggregate
        exhausted_any_lot = any(lot.remaining_quantity == Decimal("0.00") for lot in lots)
        cls._sync_product_batch(store, product, sync_prices=exhausted_any_lot)

        return created_allocations

    # =========================================================================
    # A. ALLOCATE SALE (FIFO)
    # =========================================================================
    @classmethod
    @transaction.atomic
    def allocate_sale(
        cls,
        *,
        sale_item: SaleItem,
        store: Store | None = None,
        product: Product | None = None,
        quantity: Decimal | None = None,
    ) -> list[StockAllocation]:
        """
        Allocates stock for a SaleItem following strict FIFO order.
        """
        if sale_item is None:
            raise InvalidAllocationError("sale_item must not be None.")

        # Idempotency check: prevent duplicate allocation for same SaleItem
        if StockAllocation.objects.filter(
            sale_item=sale_item, movement_type=StockAllocation.MovementType.SALE
        ).exists():
            raise AllocationConflictError(f"SaleItem #{sale_item.pk} already has stock allocations.")

        target_store = store or sale_item.sale.store
        target_product = product or sale_item.product
        target_quantity = quantity if quantity is not None else sale_item.quantity

        return cls._deduct_fifo(
            store=target_store,
            product=target_product,
            quantity=target_quantity,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            item_kwargs={"sale_item": sale_item},
        )

    # =========================================================================
    # B. REVERSE SALE RETURN (REVERSE LIFO)
    # =========================================================================
    @classmethod
    @transaction.atomic
    def reverse_sale_return(
        cls,
        *,
        sale_return_item: SaleReturnItem,
        store: Store | None = None,
        product: Product | None = None,
        quantity: Decimal | None = None,
    ) -> list[StockAllocation]:
        """
        Reverses original Sale allocations for a SaleReturnItem using strict reverse LIFO order.
        Points each return allocation to original SALE allocation via reversal_of.
        Restores lot remaining_quantity and synchronizes ProductBatch.
        """
        if sale_return_item is None:
            raise InvalidAllocationError("sale_return_item must not be None.")

        original_sale_item = sale_return_item.sale_item
        if original_sale_item is None:
            raise InvalidAllocationError(
                f"SaleReturnItem #{sale_return_item.pk} has no linked sale_item."
            )

        # Idempotency check: prevent duplicate reversal for same SaleReturnItem
        if StockAllocation.objects.filter(
            sale_return_item=sale_return_item,
            movement_type=StockAllocation.MovementType.SALE_RETURN,
        ).exists():
            raise AllocationConflictError(
                f"SaleReturnItem #{sale_return_item.pk} has already been processed."
            )

        target_store = store or sale_return_item.sale_return.store
        target_product = product or sale_return_item.product
        target_quantity = (
            quantity if quantity is not None else sale_return_item.quantity
        )
        if target_quantity is None or Decimal(str(target_quantity)) <= Decimal("0.00"):
            raise InvalidAllocationError("Return quantity must be strictly greater than zero.")
        req_qty = Decimal(str(target_quantity))

        # 1. Lock ProductBatch
        cls._lock_product_batch(target_store, target_product)

        # 2. Lock original SALE allocations for this sale_item (Reverse LIFO: created_at DESC, id DESC)
        orig_allocations = list(
            StockAllocation.objects.select_for_update()
            .filter(
                sale_item=original_sale_item,
                movement_type=StockAllocation.MovementType.SALE,
                direction=StockAllocation.Direction.OUT,
            )
            .order_by("-created_at", "-id")
        )
        if not orig_allocations:
            raise InvalidReversalError(
                f"No original SALE allocations found for SaleItem #{original_sale_item.pk}."
            )

        # 3. Calculate reversible availability per original allocation
        avail_map = {}
        total_reversible = Decimal("0.00")
        for orig in orig_allocations:
            already_reversed = (
                StockAllocation.objects.filter(reversal_of=orig).aggregate(
                    total=models.Sum("quantity")
                )["total"]
                or Decimal("0.00")
            )
            avail = orig.quantity - already_reversed
            avail = max(Decimal("0.00"), avail)
            avail_map[orig.pk] = avail
            total_reversible += avail

        if req_qty > total_reversible:
            raise InvalidReversalError(
                f"Return quantity {req_qty} exceeds available reversible quantity "
                f"{total_reversible} for SaleItem #{original_sale_item.pk}."
            )

        # 4. Reverse in Reverse LIFO order
        remaining_return = req_qty
        created_allocations = []

        for orig in orig_allocations:
            if remaining_return <= Decimal("0.00"):
                break

            avail = avail_map[orig.pk]
            if avail <= Decimal("0.00"):
                continue

            reversal_qty = min(avail, remaining_return)
            remaining_return -= reversal_qty

            # Lock and restore lot remaining_quantity
            lot = StockLot.objects.select_for_update().get(pk=orig.lot_id)
            lot.remaining_quantity += reversal_qty
            lot.save(update_fields=["remaining_quantity"])

            ret_alloc = StockAllocation.objects.create(
                lot=lot,
                movement_type=StockAllocation.MovementType.SALE_RETURN,
                direction=StockAllocation.Direction.IN,
                quantity=reversal_qty,
                unit_cost=orig.unit_cost,
                sale_return_item=sale_return_item,
                reversal_of=orig,
            )
            created_allocations.append(ret_alloc)

        # 5. Synchronize ProductBatch aggregate
        cls._sync_product_batch(target_store, target_product)

        return created_allocations

    # =========================================================================
    # C. ALLOCATE WRITE-OFF (FIFO)
    # =========================================================================
    @classmethod
    @transaction.atomic
    def allocate_write_off(
        cls,
        *,
        write_off_item: WriteOffItem,
        store: Store | None = None,
        product: Product | None = None,
        quantity: Decimal | None = None,
    ) -> list[StockAllocation]:
        """
        Deducts stock for a WriteOffItem following strict FIFO order.
        """
        if write_off_item is None:
            raise InvalidAllocationError("write_off_item must not be None.")

        # Idempotency check
        if StockAllocation.objects.filter(
            write_off_item=write_off_item,
            movement_type=StockAllocation.MovementType.WRITE_OFF,
        ).exists():
            raise AllocationConflictError(
                f"WriteOffItem #{write_off_item.pk} already has stock allocations."
            )

        target_store = store or write_off_item.write_off.store
        target_product = product or write_off_item.product
        target_quantity = quantity if quantity is not None else write_off_item.quantity

        return cls._deduct_fifo(
            store=target_store,
            product=target_product,
            quantity=target_quantity,
            movement_type=StockAllocation.MovementType.WRITE_OFF,
            direction=StockAllocation.Direction.OUT,
            item_kwargs={"write_off_item": write_off_item},
        )

    # =========================================================================
    # D. ALLOCATE SUPPLIER RETURN (FIFO)
    # =========================================================================
    @classmethod
    @transaction.atomic
    def allocate_supplier_return(
        cls,
        *,
        supplier_return_item,
        store: Store | None = None,
        product: Product | None = None,
        quantity: Decimal | None = None,
        supplier=None,
    ) -> list[StockAllocation]:
        """
        Deducts stock for a Supplier Return (StockEntryReturnItem) following FIFO from purchase lots.
        Preserves supplier lineage by prioritizing lots originating from the supplier.
        """
        if supplier_return_item is None:
            raise InvalidAllocationError("supplier_return_item must not be None.")

        # Idempotency check
        if StockAllocation.objects.filter(
            supplier_return_item=supplier_return_item,
            movement_type=StockAllocation.MovementType.SUPPLIER_RETURN,
        ).exists():
            raise AllocationConflictError(
                f"StockEntryReturnItem #{supplier_return_item.pk} already has stock allocations."
            )

        target_store = (
            store
            or getattr(getattr(supplier_return_item, "stock_return", None), "entry", None).store
        )
        target_product = product or supplier_return_item.product
        target_quantity = (
            quantity if quantity is not None else supplier_return_item.quantity
        )
        target_supplier = supplier or getattr(
            getattr(supplier_return_item, "stock_return", None), "entry", None
        ).supplier

        # Prioritize lots matching supplier and PURCHASE lot_type
        lot_filter = {"lot_type": StockLot.LotType.PURCHASE}
        if target_supplier:
            # Check if supplier-specific purchase lots have enough stock
            avail_sup = (
                StockLot.objects.filter(
                    store=target_store,
                    product=target_product,
                    supplier=target_supplier,
                    lot_type=StockLot.LotType.PURCHASE,
                    remaining_quantity__gt=Decimal("0.00"),
                ).aggregate(total=models.Sum("remaining_quantity"))["total"]
                or Decimal("0.00")
            )
            if avail_sup >= Decimal(str(target_quantity)):
                lot_filter["supplier"] = target_supplier

        return cls._deduct_fifo(
            store=target_store,
            product=target_product,
            quantity=target_quantity,
            movement_type=StockAllocation.MovementType.SUPPLIER_RETURN,
            direction=StockAllocation.Direction.OUT,
            item_kwargs={"supplier_return_item": supplier_return_item},
            lot_queryset_filter=lot_filter,
        )

    # =========================================================================
    # E. ALLOCATE TRANSFER OUT (FIFO)
    # =========================================================================
    @classmethod
    @transaction.atomic
    def allocate_transfer_out(
        cls,
        *,
        transfer_item: StockTransferItem,
        store: Store | None = None,
        product: Product | None = None,
        quantity: Decimal | None = None,
    ) -> list[StockAllocation]:
        """
        Deducts stock from source store for a StockTransferItem following FIFO order.
        """
        if transfer_item is None:
            raise InvalidAllocationError("transfer_item must not be None.")

        # Idempotency check
        if StockAllocation.objects.filter(
            transfer_item=transfer_item,
            movement_type=StockAllocation.MovementType.TRANSFER_OUT,
        ).exists():
            raise AllocationConflictError(
                f"StockTransferItem #{transfer_item.pk} already has TRANSFER_OUT allocations."
            )

        source_store = store or transfer_item.stock_transfer.from_store
        target_product = product or transfer_item.product
        target_quantity = quantity if quantity is not None else transfer_item.quantity

        allocations = cls._deduct_fifo(
            store=source_store,
            product=target_product,
            quantity=target_quantity,
            movement_type=StockAllocation.MovementType.TRANSFER_OUT,
            direction=StockAllocation.Direction.OUT,
            item_kwargs={"transfer_item": transfer_item},
        )

        # Synchronize transfer_item price snapshot to match actual allocated FIFO lots
        if transfer_item and allocations:
            total_qty = sum((a.quantity for a in allocations), Decimal("0.00"))
            if total_qty > Decimal("0.00"):
                total_cost = sum((a.quantity * a.unit_cost for a in allocations), Decimal("0.00"))
                avg_cost = (total_cost / total_qty).quantize(Decimal("0.01"))
                total_selling = sum((a.quantity * a.lot.resolve_selling_price() for a in allocations), Decimal("0.00"))
                avg_selling = (total_selling / total_qty).quantize(Decimal("0.01"))
                if avg_selling <= Decimal("0.00"):
                    if transfer_item.selling_price and transfer_item.selling_price > Decimal("0.00"):
                        avg_selling = transfer_item.selling_price
                    else:
                        from_batch = ProductBatch.objects.filter(store=source_store, product=target_product).first()
                        if from_batch and from_batch.selling_price:
                            avg_selling = from_batch.selling_price

                if transfer_item.purchase_price != avg_cost or transfer_item.selling_price != avg_selling:
                    transfer_item.purchase_price = avg_cost
                    transfer_item.selling_price = avg_selling
                    transfer_item.save(update_fields=["purchase_price", "selling_price"])

        return allocations

    # =========================================================================
    # F. ALLOCATE TRANSFER IN
    # =========================================================================
    @classmethod
    @transaction.atomic
    def allocate_transfer_in(
        cls,
        *,
        transfer_item: StockTransferItem,
        store: Store | None = None,
        product: Product | None = None,
        quantity: Decimal | None = None,
    ) -> list[StockAllocation]:
        """
        Receives transferred stock at destination store.
        Creates destination StockLot for each source lot (preserving lineage, supplier, and unit_cost).
        Creates TRANSFER_IN StockAllocation ledger entry without double counting.
        """
        if transfer_item is None:
            raise InvalidAllocationError("transfer_item must not be None.")

        # Idempotency check
        if StockAllocation.objects.filter(
            transfer_item=transfer_item,
            movement_type=StockAllocation.MovementType.TRANSFER_IN,
        ).exists():
            raise AllocationConflictError(
                f"StockTransferItem #{transfer_item.pk} already has TRANSFER_IN allocations."
            )

        to_store = store or transfer_item.stock_transfer.to_store
        target_product = product or transfer_item.product

        # Retrieve the TRANSFER_OUT allocations that fulfilled this transfer_item
        out_allocations = list(
            StockAllocation.objects.select_for_update()
            .filter(
                transfer_item=transfer_item,
                movement_type=StockAllocation.MovementType.TRANSFER_OUT,
            )
            .order_by("created_at", "id")
        )
        if not out_allocations:
            raise InvalidAllocationError(
                f"No TRANSFER_OUT allocations found for StockTransferItem #{transfer_item.pk}. "
                f"Cannot receive transfer before sending."
            )

        # 1. Lock destination ProductBatch
        cls._lock_product_batch(to_store, target_product)
        cls.ensure_lot_coverage(to_store, target_product)

        had_prior_active_stock = StockLot.objects.filter(
            store=to_store,
            product=target_product,
            remaining_quantity__gt=Decimal("0.00"),
        ).exists()

        created_in_allocations = []
        for out_alloc in out_allocations:
            # Create corresponding TRANSFER_IN lot in destination store
            dest_lot = StockLot.objects.create(
                store=to_store,
                product=target_product,
                supplier=out_alloc.lot.supplier,
                lot_type=StockLot.LotType.TRANSFER_IN,
                source_lot=out_alloc.lot,
                initial_quantity=out_alloc.quantity,
                remaining_quantity=out_alloc.quantity,
                purchase_price=out_alloc.unit_cost,
            )

            # Record TRANSFER_IN ledger entry
            in_alloc = StockAllocation.objects.create(
                lot=dest_lot,
                movement_type=StockAllocation.MovementType.TRANSFER_IN,
                direction=StockAllocation.Direction.IN,
                quantity=out_alloc.quantity,
                unit_cost=out_alloc.unit_cost,
                transfer_item=transfer_item,
            )
            created_in_allocations.append(in_alloc)

        # 2. Synchronize destination ProductBatch
        batch = cls._sync_product_batch(to_store, target_product)

        # 3. Synchronize destination ProductBatch current retail selling price & wholesale price from transfer
        if created_in_allocations:
            latest_dest_lot = created_in_allocations[-1].lot
            inherited_selling = latest_dest_lot.resolve_selling_price()
            if not inherited_selling or inherited_selling <= Decimal("0.00"):
                if transfer_item and transfer_item.selling_price and transfer_item.selling_price > Decimal("0.00"):
                    inherited_selling = transfer_item.selling_price

            inherited_wholesale = latest_dest_lot.resolve_wholesale_price()

            up_fields = []
            if inherited_selling is not None and inherited_selling > Decimal("0.00"):
                if batch.selling_price != inherited_selling:
                    batch.selling_price = inherited_selling
                    up_fields.append("selling_price")

            if inherited_wholesale is not None and inherited_wholesale > Decimal("0.00"):
                if batch.wholesale_price != inherited_wholesale:
                    batch.wholesale_price = inherited_wholesale
                    up_fields.append("wholesale_price")

            if not had_prior_active_stock:
                inherited_purchase = latest_dest_lot.purchase_price
                if inherited_purchase is not None and inherited_purchase > Decimal("0.00"):
                    if batch.purchase_price != inherited_purchase:
                        batch.purchase_price = inherited_purchase
                        up_fields.append("purchase_price")

            if up_fields:
                batch.save(update_fields=up_fields)

        return created_in_allocations

    # =========================================================================
    # G. ALLOCATE INVENTORY SHORTAGE (FIFO)
    # =========================================================================
    @classmethod
    @transaction.atomic
    def allocate_inventory_shortage(
        cls,
        *,
        inventory_session: InventorySession,
        product: Product,
        quantity: Decimal,
        store: Store | None = None,
        description: str = "",
    ) -> list[StockAllocation]:
        """
        Deducts missing inventory stock following FIFO order.
        Strictly requires inventory_session and sets operational FKs to NULL.
        """
        if inventory_session is None:
            raise InvalidAllocationError("inventory_session must not be None.")
        if product is None:
            raise InvalidAllocationError("product must not be None.")

        target_store = store or inventory_session.store

        return cls._deduct_fifo(
            store=target_store,
            product=product,
            quantity=quantity,
            movement_type=StockAllocation.MovementType.INVENTORY_SHORTAGE,
            direction=StockAllocation.Direction.OUT,
            item_kwargs={"inventory_session": inventory_session},
            description=description,
        )

    # =========================================================================
    # H. CREATE INVENTORY EXCESS LOT
    # =========================================================================
    @classmethod
    @transaction.atomic
    def create_inventory_excess_lot(
        cls,
        *,
        inventory_session: InventorySession,
        product: Product,
        quantity: Decimal,
        store: Store | None = None,
        purchase_price: Decimal | None = None,
        description: str = "",
    ) -> InventoryExcessResult:
        """
        Creates a new StockLot (lot_type=INVENTORY_EXCESS) and StockAllocation (movement_type=INVENTORY_EXCESS)
        for surplus stock found during inventory count.
        """
        if inventory_session is None:
            raise InvalidAllocationError("inventory_session must not be None.")
        if product is None:
            raise InvalidAllocationError("product must not be None.")
        if quantity is None or Decimal(str(quantity)) <= Decimal("0.00"):
            raise InvalidAllocationError("Excess quantity must be strictly greater than zero.")

        target_store = store or inventory_session.store
        excess_qty = Decimal(str(quantity))

        # 1. Lock ProductBatch
        cls._lock_product_batch(target_store, product)
        cls.ensure_lot_coverage(target_store, product)

        # 2. Determine purchase price
        if purchase_price is None:
            batch = ProductBatch.objects.filter(store=target_store, product=product).first()
            if batch and batch.purchase_price > Decimal("0.00"):
                cost = batch.purchase_price
            else:
                latest_lot = (
                    StockLot.objects.filter(store=target_store, product=product)
                    .order_by("-created_at", "-id")
                    .first()
                )
                if latest_lot and latest_lot.purchase_price > Decimal("0.00"):
                    cost = latest_lot.purchase_price
                else:
                    cost = getattr(product, "purchase_price", Decimal("0.00")) or Decimal("0.00")
        else:
            cost = Decimal(str(purchase_price))

        # 3. Create INVENTORY_EXCESS lot
        excess_lot = StockLot.objects.create(
            store=target_store,
            product=product,
            supplier=None,
            lot_type=StockLot.LotType.INVENTORY_EXCESS,
            initial_quantity=excess_qty,
            remaining_quantity=excess_qty,
            purchase_price=cost,
        )

        # 4. Record INVENTORY_EXCESS ledger allocation
        excess_alloc = StockAllocation.objects.create(
            lot=excess_lot,
            movement_type=StockAllocation.MovementType.INVENTORY_EXCESS,
            direction=StockAllocation.Direction.IN,
            quantity=excess_qty,
            unit_cost=cost,
            inventory_session=inventory_session,
            description=description,
        )

        # 5. Synchronize ProductBatch aggregate
        cls._sync_product_batch(target_store, product)

        return InventoryExcessResult(lot=excess_lot, allocation=excess_alloc)

    # =========================================================================
    # H. PRICE RESOLUTION HELPERS
    # =========================================================================
    @classmethod
    def resolve_selling_price(cls, store: Store, product: Product) -> Decimal:
        """
        Resolves authoritative current retail selling price for (store, product).
        1. Checks ProductBatch.selling_price (current store active retail price).
        2. If no batch or batch.selling_price <= 0, checks most recent lot's resolve_selling_price().
        3. Fallback: product.price or Decimal("0.00").
        """
        batch = ProductBatch.objects.filter(store=store, product=product).first()
        if batch and batch.selling_price and batch.selling_price > Decimal("0.00"):
            return batch.selling_price

        recent_lot = (
            StockLot.objects.filter(
                store=store,
                product=product,
                remaining_quantity__gt=Decimal("0.00"),
            )
            .select_related("stock_entry_item", "source_lot")
            .order_by("-created_at", "-id")
            .first()
        )
        if not recent_lot:
            recent_lot = (
                StockLot.objects.filter(store=store, product=product)
                .select_related("stock_entry_item", "source_lot")
                .order_by("-created_at", "-id")
                .first()
            )

        if recent_lot:
            price = recent_lot.resolve_selling_price()
            if price > Decimal("0.00"):
                return price

        prod_price = getattr(product, "price", Decimal("0.00")) or Decimal("0.00")
        return prod_price

    @classmethod
    def resolve_purchase_price(cls, store: Store, product: Product) -> Decimal:
        """
        Resolves authoritative purchase price (cost) for (store, product).
        """
        active_lot = (
            StockLot.objects.filter(
                store=store,
                product=product,
                remaining_quantity__gt=Decimal("0.00"),
            )
            .order_by("created_at", "id")
            .first()
        )
        if active_lot and active_lot.purchase_price > Decimal("0.00"):
            return active_lot.purchase_price

        batch = ProductBatch.objects.filter(store=store, product=product).first()
        if batch and batch.purchase_price and batch.purchase_price > Decimal("0.00"):
            return batch.purchase_price

        recent_lot = (
            StockLot.objects.filter(store=store, product=product)
            .order_by("-created_at", "-id")
            .first()
        )
        if recent_lot and recent_lot.purchase_price > Decimal("0.00"):
            return recent_lot.purchase_price

        return Decimal("0.00")

