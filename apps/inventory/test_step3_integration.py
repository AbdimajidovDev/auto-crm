import decimal
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import models
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.contract.models import StockEntry, StockEntryItem, StockEntryPayment, Supplier
from apps.contract.services.stock_entry_return_service import StockEntryReturnService
from apps.contract.services.stock_entry_service import StockEntryService
from apps.inventory.models import (
    InventoryCount,
    InventoryMovement,
    InventorySession,
    InventorySnapshot,
    StockAllocation,
    StockLot,
)
from apps.inventory.services.inventory_service import InventoryService
from apps.products.models import Product, ProductBatch
from apps.sales.models import Payment, Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.sales.services.sale_return_service import SaleReturnService
from apps.sales.services.sales_services import SaleService
from apps.store.models import Store, StoreUser
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.transfer.services.transfer_service import TransferService
from apps.users.models.customers import Customer
from apps.writeoff.models import WriteOff, WriteOffItem
from apps.writeoff.services.write_off_service import WriteOffService

User = get_user_model()


class StockInvariantMixin:
    """Helper mixin to verify the authoritative invariant: ProductBatch.quantity == SUM(StockLot.remaining_quantity)."""

    def assert_stock_invariant(self, store_id: int, product_id: int, msg: str = "") -> None:
        batch = ProductBatch.objects.filter(store_id=store_id, product_id=product_id).first()
        batch_qty = batch.quantity if batch else Decimal("0.00")
        total_lot_qty = (
            StockLot.objects.filter(store_id=store_id, product_id=product_id).aggregate(
                total=models.Sum("remaining_quantity")
            )["total"]
            or Decimal("0.00")
        )
        self.assertEqual(
            batch_qty,
            total_lot_qty,
            f"Stock Invariant Violated for store={store_id}, product={product_id}: "
            f"ProductBatch.quantity ({batch_qty}) != SUM(StockLot.remaining_quantity) ({total_lot_qty}). {msg}",
        )


class Step3BusinessFlowIntegrationTests(StockInvariantMixin, TestCase):
    """
    Comprehensive Step 3 integration tests covering all 6 business flows:
    1. StockEntry
    2. Sale
    3. SaleReturn
    4. WriteOff
    5. StockTransfer
    6. InventorySession finalize
    7. Supplier Return
    8. Full sequential lifecycle
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(
            phone_number="+998901234567",
            full_name="Admin User",
            is_superuser=True,
        )
        self.store1 = Store.objects.create(
            name="Store Alpha",
            address="Tashkent, Alpha st.",
            phone_number="+998901111111",
            type=Store.StoreType.STORE,
        )
        self.store2 = Store.objects.create(
            name="Store Beta",
            address="Tashkent, Beta st.",
            phone_number="+998902222222",
            type=Store.StoreType.STORE,
        )
        StoreUser.objects.create(user=self.user, store=self.store1, is_active=True)
        StoreUser.objects.create(user=self.user, store=self.store2, is_active=True)

        self.supplier = Supplier.objects.create(name="Global Auto Parts LLC")
        self.customer = Customer.objects.create(
            full_name="Alisher Navoiy",
            phone_number="+998909999999",
        )
        self.product1 = Product.objects.create(
            name="Brake Pads Front",
            barcode="4781001000018",
            status=Product.ProductStatus.ACTIVE,
        )
        self.product2 = Product.objects.create(
            name="Engine Oil 5W-30",
            barcode="4781001000025",
            status=Product.ProductStatus.ACTIVE,
        )

    # =========================================================================
    # 1. STOCK ENTRY INTEGRATION
    # =========================================================================
    def test_stock_entry_creates_lots_and_maintains_invariant(self):
        """StockEntryService.create_entry creates StockLots, preserves supplier, and syncs ProductBatch."""
        entry = StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[
                {
                    "product": self.product1,
                    "quantity": Decimal("10.00"),
                    "purchase_price": Decimal("100.00"),
                    "selling_price": Decimal("150.00"),
                    "wholesale_price": Decimal("130.00"),
                },
                {
                    "product": self.product2,
                    "quantity": Decimal("20.00"),
                    "purchase_price": Decimal("50.00"),
                    "selling_price": Decimal("80.00"),
                    "wholesale_price": Decimal("70.00"),
                },
            ],
            cash_amount=Decimal("2000.00"),
        )
        self.assertIsNotNone(entry.pk)

        # Check StockLot for product1
        lot1 = StockLot.objects.get(store=self.store1, product=self.product1)
        self.assertEqual(lot1.lot_type, StockLot.LotType.PURCHASE)
        self.assertEqual(lot1.supplier, self.supplier)
        self.assertEqual(lot1.initial_quantity, Decimal("10.00"))
        self.assertEqual(lot1.remaining_quantity, Decimal("10.00"))
        self.assertEqual(lot1.purchase_price, Decimal("100.00"))
        self.assertEqual(lot1.stock_entry_item.entry, entry)

        # Check StockLot for product2
        lot2 = StockLot.objects.get(store=self.store1, product=self.product2)
        self.assertEqual(lot2.lot_type, StockLot.LotType.PURCHASE)
        self.assertEqual(lot2.supplier, self.supplier)
        self.assertEqual(lot2.initial_quantity, Decimal("20.00"))
        self.assertEqual(lot2.remaining_quantity, Decimal("20.00"))

        # Verify invariant on both products
        self.assert_stock_invariant(self.store1.id, self.product1.id)
        self.assert_stock_invariant(self.store1.id, self.product2.id)

        # Ensure NO double-counting movement allocation was created for purchase
        self.assertFalse(StockAllocation.objects.filter(lot=lot1).exists())
        self.assertFalse(StockAllocation.objects.filter(lot=lot2).exists())

    # =========================================================================
    # 2. SALE INTEGRATION (FIFO & MULTI-LOT)
    # =========================================================================
    def test_sale_fifo_deduction_and_invariant(self):
        """SaleService.create_sale executes FIFO allocation across multiple lots and syncs ProductBatch."""
        # Entry 1: 5 units @ 100
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[{
                "product": self.product1,
                "quantity": Decimal("5.00"),
                "purchase_price": Decimal("100.00"),
                "selling_price": Decimal("160.00"),
                "wholesale_price": Decimal("140.00"),
            }],
            cash_amount=Decimal("500.00"),
        )
        # Entry 2: 5 units @ 120
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[{
                "product": self.product1,
                "quantity": Decimal("5.00"),
                "purchase_price": Decimal("120.00"),
                "selling_price": Decimal("180.00"),
                "wholesale_price": Decimal("150.00"),
            }],
            cash_amount=Decimal("600.00"),
        )
        self.assert_stock_invariant(self.store1.id, self.product1.id)

        # Sale: 7 units
        sale = SaleService.create_sale(
            user=self.user,
            data={
                "store": self.store1.id,
                "customer": self.customer.id,
                "items": [{
                    "product": self.product1.id,
                    "quantity": Decimal("7.00"),
                    "price": Decimal("180.00"),
                }],
                "payments": [{"type": "cash", "amount": Decimal("1260.00")}],
            },
        )
        self.assertEqual(sale.status, Sale.Status.PAID)

        sale_item = sale.items.get()
        allocations = list(sale_item.stock_allocations.order_by("created_at", "id"))
        self.assertEqual(len(allocations), 2)

        # First allocation: 5 units from lot 1 @ 100
        self.assertEqual(allocations[0].quantity, Decimal("5.00"))
        self.assertEqual(allocations[0].unit_cost, Decimal("100.00"))
        self.assertEqual(allocations[0].direction, StockAllocation.Direction.OUT)
        self.assertEqual(allocations[0].lot.remaining_quantity, Decimal("0.00"))

        # Second allocation: 2 units from lot 2 @ 120
        self.assertEqual(allocations[1].quantity, Decimal("2.00"))
        self.assertEqual(allocations[1].unit_cost, Decimal("120.00"))
        self.assertEqual(allocations[1].direction, StockAllocation.Direction.OUT)
        self.assertEqual(allocations[1].lot.remaining_quantity, Decimal("3.00"))

        # Weighted average purchase price on sale_item: (5*100 + 2*120) / 7 = 740 / 7 = 105.71
        self.assertEqual(sale_item.purchase_price, Decimal("105.71"))

        # Verify invariant
        self.assert_stock_invariant(self.store1.id, self.product1.id)
        batch = ProductBatch.objects.get(store=self.store1, product=self.product1)
        self.assertEqual(batch.quantity, Decimal("3.00"))

    def test_sale_insufficient_stock_causes_atomic_rollback(self):
        """SaleService.create_sale rolls back entirely if stock is insufficient."""
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[{
                "product": self.product1,
                "quantity": Decimal("4.00"),
                "purchase_price": Decimal("100.00"),
                "selling_price": Decimal("150.00"),
                "wholesale_price": Decimal("130.00"),
            }],
            cash_amount=Decimal("400.00"),
        )
        self.assert_stock_invariant(self.store1.id, self.product1.id)

        with self.assertRaises(ValidationError):
            SaleService.create_sale(
                user=self.user,
                data={
                    "store": self.store1.id,
                    "customer": self.customer.id,
                    "items": [{
                        "product": self.product1.id,
                        "quantity": Decimal("10.00"),
                        "price": Decimal("150.00"),
                    }],
                    "payments": [{"type": "cash", "amount": Decimal("1500.00")}],
                },
            )

        # Invariant preserved, lots untouched
        self.assert_stock_invariant(self.store1.id, self.product1.id)
        batch = ProductBatch.objects.get(store=self.store1, product=self.product1)
        self.assertEqual(batch.quantity, Decimal("4.00"))
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(StockAllocation.objects.count(), 0)

    # =========================================================================
    # 3. SALE RETURN INTEGRATION (REVERSE LIFO)
    # =========================================================================
    def test_sale_return_reverse_lifo_and_reversal_of_linking(self):
        """SaleReturnService.create_return executes Reverse LIFO restoration and links reversal_of."""
        # Setup: 2 lots (lot 1: 5 @ 100, lot 2: 5 @ 120)
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[{
                "product": self.product1,
                "quantity": Decimal("5.00"),
                "purchase_price": Decimal("100.00"),
                "selling_price": Decimal("150.00"),
                "wholesale_price": Decimal("130.00"),
            }],
            cash_amount=Decimal("500.00"),
        )
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[{
                "product": self.product1,
                "quantity": Decimal("5.00"),
                "purchase_price": Decimal("120.00"),
                "selling_price": Decimal("150.00"),
                "wholesale_price": Decimal("130.00"),
            }],
            cash_amount=Decimal("600.00"),
        )
        # Sell 7 units (5 from lot 1, 2 from lot 2)
        sale = SaleService.create_sale(
            user=self.user,
            data={
                "store": self.store1.id,
                "customer": self.customer.id,
                "items": [{
                    "product": self.product1.id,
                    "quantity": Decimal("7.00"),
                    "price": Decimal("150.00"),
                }],
                "payments": [{"type": "cash", "amount": Decimal("1050.00")}],
            },
        )
        sale_item = sale.items.get()
        self.assert_stock_invariant(self.store1.id, self.product1.id)

        # Return 3 units (should restore 2 to lot 2, and 1 to lot 1 via Reverse LIFO)
        ret = SaleReturnService.create_return(
            user=self.user,
            data={
                "sale": sale.id,
                "items": [{"sale_item": sale_item.id, "quantity": Decimal("3.00")}],
                "comment": "Customer return 3 units",
            },
        )
        self.assertIsNotNone(ret.pk)

        return_item = ret.items.get()
        return_allocations = list(return_item.stock_allocations.order_by("created_at", "id"))
        self.assertEqual(len(return_allocations), 2)

        # Reverse 1: 2 units back to lot 2
        self.assertEqual(return_allocations[0].quantity, Decimal("2.00"))
        self.assertEqual(return_allocations[0].direction, StockAllocation.Direction.IN)
        self.assertEqual(return_allocations[0].reversal_of.unit_cost, Decimal("120.00"))
        self.assertEqual(return_allocations[0].lot.remaining_quantity, Decimal("5.00"))

        # Reverse 2: 1 unit back to lot 1
        self.assertEqual(return_allocations[1].quantity, Decimal("1.00"))
        self.assertEqual(return_allocations[1].direction, StockAllocation.Direction.IN)
        self.assertEqual(return_allocations[1].reversal_of.unit_cost, Decimal("100.00"))
        self.assertEqual(return_allocations[1].lot.remaining_quantity, Decimal("1.00"))

        # Verify invariant (5.00 in lot 2 + 1.00 in lot 1 = 6.00 in batch)
        self.assert_stock_invariant(self.store1.id, self.product1.id)
        batch = ProductBatch.objects.get(store=self.store1, product=self.product1)
        self.assertEqual(batch.quantity, Decimal("6.00"))

    def test_sale_return_over_return_rejected(self):
        """SaleReturnService.create_return rejects over-return attempts."""
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[{
                "product": self.product1,
                "quantity": Decimal("5.00"),
                "purchase_price": Decimal("100.00"),
                "selling_price": Decimal("150.00"),
                "wholesale_price": Decimal("130.00"),
            }],
            cash_amount=Decimal("500.00"),
        )
        sale = SaleService.create_sale(
            user=self.user,
            data={
                "store": self.store1.id,
                "customer": self.customer.id,
                "items": [{
                    "product": self.product1.id,
                    "quantity": Decimal("2.00"),
                    "price": Decimal("150.00"),
                }],
                "payments": [{"type": "cash", "amount": Decimal("300.00")}],
            },
        )
        sale_item = sale.items.get()

        with self.assertRaises(ValidationError):
            SaleReturnService.create_return(
                user=self.user,
                data={
                    "sale": sale.id,
                    "items": [{"sale_item": sale_item.id, "quantity": Decimal("5.00")}],
                },
            )
        self.assert_stock_invariant(self.store1.id, self.product1.id)

    # =========================================================================
    # 4. WRITE-OFF INTEGRATION
    # =========================================================================
    def test_write_off_fifo_deduction_and_invariant(self):
        """WriteOffService.create_write_off executes FIFO deductions and syncs ProductBatch."""
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[
                {
                    "product": self.product1,
                    "quantity": Decimal("4.00"),
                    "purchase_price": Decimal("100.00"),
                    "selling_price": Decimal("150.00"),
                    "wholesale_price": Decimal("130.00"),
                },
            ],
            cash_amount=Decimal("400.00"),
        )
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[
                {
                    "product": self.product1,
                    "quantity": Decimal("6.00"),
                    "purchase_price": Decimal("110.00"),
                    "selling_price": Decimal("150.00"),
                    "wholesale_price": Decimal("130.00"),
                },
            ],
            cash_amount=Decimal("660.00"),
        )
        self.assert_stock_invariant(self.store1.id, self.product1.id)

        # Write-off: 6 units (4 from lot 1 @ 100, 2 from lot 2 @ 110)
        woff = WriteOffService.create_write_off(
            store=self.store1,
            items=[{"product": self.product1, "quantity": Decimal("6.00")}],
            reason=WriteOff.Reason.DAMAGED,
            comment="Damaged in transit",
            user=self.user,
        )
        self.assertIsNotNone(woff.pk)

        woff_item = woff.items.get()
        allocations = list(woff_item.stock_allocations.order_by("created_at", "id"))
        self.assertEqual(len(allocations), 2)
        self.assertEqual(allocations[0].quantity, Decimal("4.00"))
        self.assertEqual(allocations[0].unit_cost, Decimal("100.00"))
        self.assertEqual(allocations[1].quantity, Decimal("2.00"))
        self.assertEqual(allocations[1].unit_cost, Decimal("110.00"))

        # Actual weighted cost: (4*100 + 2*110)/6 = 620/6 = 103.33
        self.assertEqual(woff_item.purchase_price, Decimal("103.33"))

        # Invariant verified: 4 remaining in lot 2
        self.assert_stock_invariant(self.store1.id, self.product1.id)
        batch = ProductBatch.objects.get(store=self.store1, product=self.product1)
        self.assertEqual(batch.quantity, Decimal("4.00"))

    # =========================================================================
    # 5. STOCK TRANSFER INTEGRATION
    # =========================================================================
    def test_stock_transfer_out_in_lineage_and_invariants(self):
        """TransferService.approve_transfer creates OUT allocations, destination lots with lineage, and preserves invariants."""
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[{
                "product": self.product1,
                "quantity": Decimal("10.00"),
                "purchase_price": Decimal("85.00"),
                "selling_price": Decimal("140.00"),
                "wholesale_price": Decimal("120.00"),
            }],
            cash_amount=Decimal("850.00"),
        )
        self.assert_stock_invariant(self.store1.id, self.product1.id)

        # Create transfer from store1 to store2 of 4 units
        transfer = TransferService.create_transfer(
            from_store=self.store1,
            to_store=self.store2,
            items_data=[{"product": self.product1, "quantity": Decimal("4.00")}],
            user=self.user,
        )
        self.assertEqual(transfer.status, StockTransfer.Status.PENDING)

        # Approve transfer
        approved = TransferService.approve_transfer(transfer_id=transfer.id, user=self.user)
        self.assertEqual(approved.status, StockTransfer.Status.APPROVED)

        transfer_item = transfer.items.get()
        out_alloc = StockAllocation.objects.get(
            transfer_item=transfer_item,
            movement_type=StockAllocation.MovementType.TRANSFER_OUT,
        )
        in_alloc = StockAllocation.objects.get(
            transfer_item=transfer_item,
            movement_type=StockAllocation.MovementType.TRANSFER_IN,
        )

        self.assertEqual(out_alloc.quantity, Decimal("4.00"))
        self.assertEqual(out_alloc.direction, StockAllocation.Direction.OUT)
        self.assertEqual(in_alloc.quantity, Decimal("4.00"))
        self.assertEqual(in_alloc.direction, StockAllocation.Direction.IN)

        # Destination lot assertions
        dest_lot = in_alloc.lot
        self.assertEqual(dest_lot.store, self.store2)
        self.assertEqual(dest_lot.supplier, self.supplier)
        self.assertEqual(dest_lot.source_lot, out_alloc.lot)
        self.assertEqual(dest_lot.initial_quantity, Decimal("4.00"))
        self.assertEqual(dest_lot.remaining_quantity, Decimal("4.00"))
        self.assertEqual(dest_lot.purchase_price, Decimal("85.00"))

        # Verify invariants on BOTH stores
        self.assert_stock_invariant(self.store1.id, self.product1.id)
        self.assert_stock_invariant(self.store2.id, self.product1.id)

        source_batch = ProductBatch.objects.get(store=self.store1, product=self.product1)
        dest_batch = ProductBatch.objects.get(store=self.store2, product=self.product1)
        self.assertEqual(source_batch.quantity, Decimal("6.00"))
        self.assertEqual(dest_batch.quantity, Decimal("4.00"))

    # =========================================================================
    # 6. INVENTORY FINALIZE INTEGRATION (SHORTAGE & EXCESS)
    # =========================================================================
    def test_inventory_finalize_shortage_and_excess_integration(self):
        """InventoryService.finalize executes FIFO shortage deduction and creates excess lots with invariants."""
        # Setup: product1 has 10 units, product2 has 5 units
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[
                {
                    "product": self.product1,
                    "quantity": Decimal("10.00"),
                    "purchase_price": Decimal("100.00"),
                    "selling_price": Decimal("150.00"),
                    "wholesale_price": Decimal("130.00"),
                },
                {
                    "product": self.product2,
                    "quantity": Decimal("5.00"),
                    "purchase_price": Decimal("50.00"),
                    "selling_price": Decimal("80.00"),
                    "wholesale_price": Decimal("70.00"),
                },
            ],
            cash_amount=Decimal("1250.00"),
        )
        self.assert_stock_invariant(self.store1.id, self.product1.id)
        self.assert_stock_invariant(self.store1.id, self.product2.id)

        # Start inventory session
        session = InventoryService.start_session(user=self.user, store_id=self.store1.id)
        self.assertEqual(session.status, InventorySession.Status.ACTIVE)

        # Sanoq: product1 has shortage (counted 7 instead of 10)
        InventoryService.set_count(
            session_id=session.id,
            product_id=self.product1.id,
            quantity=Decimal("7.00"),
        )
        # Sanoq: product2 has excess (counted 8 instead of 5)
        InventoryService.set_count(
            session_id=session.id,
            product_id=self.product2.id,
            quantity=Decimal("8.00"),
        )

        # Finalize inventory session
        InventoryService.finalize(session_id=session.id)
        session.refresh_from_db()
        self.assertEqual(session.status, InventorySession.Status.COMPLETED)

        # Verify Shortage for product1
        shortage_alloc = StockAllocation.objects.get(
            inventory_session=session,
            lot__product=self.product1,
            movement_type=StockAllocation.MovementType.INVENTORY_SHORTAGE,
        )
        self.assertEqual(shortage_alloc.quantity, Decimal("3.00"))
        self.assertEqual(shortage_alloc.direction, StockAllocation.Direction.OUT)
        self.assertIsNone(shortage_alloc.sale_item)
        self.assertIsNone(shortage_alloc.write_off_item)

        # Verify Excess for product2
        excess_alloc = StockAllocation.objects.get(
            inventory_session=session,
            lot__product=self.product2,
            movement_type=StockAllocation.MovementType.INVENTORY_EXCESS,
        )
        self.assertEqual(excess_alloc.quantity, Decimal("3.00"))
        self.assertEqual(excess_alloc.direction, StockAllocation.Direction.IN)
        excess_lot = excess_alloc.lot
        self.assertEqual(excess_lot.lot_type, StockLot.LotType.INVENTORY_EXCESS)
        self.assertIsNone(excess_lot.supplier)  # Excess supplier is NULL per requirements
        self.assertEqual(excess_lot.remaining_quantity, Decimal("3.00"))

        # Verify invariants on BOTH products
        self.assert_stock_invariant(self.store1.id, self.product1.id)
        self.assert_stock_invariant(self.store1.id, self.product2.id)

        batch1 = ProductBatch.objects.get(store=self.store1, product=self.product1)
        batch2 = ProductBatch.objects.get(store=self.store1, product=self.product2)
        self.assertEqual(batch1.quantity, Decimal("7.00"))
        self.assertEqual(batch2.quantity, Decimal("8.00"))

    # =========================================================================
    # 7. SUPPLIER RETURN INTEGRATION
    # =========================================================================
    def test_supplier_return_fifo_deduction_and_invariant(self):
        """StockEntryReturnService.create_return executes SUPPLIER_RETURN FIFO deduction and preserves invariants."""
        entry = StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[{
                "product": self.product1,
                "quantity": Decimal("8.00"),
                "purchase_price": Decimal("90.00"),
                "selling_price": Decimal("140.00"),
                "wholesale_price": Decimal("120.00"),
            }],
            cash_amount=Decimal("720.00"),
        )
        self.assert_stock_invariant(self.store1.id, self.product1.id)

        entry_item = entry.items.get()
        ret = StockEntryReturnService.create_return(
            entry=entry,
            items=[{"entry_item": entry_item, "quantity": Decimal("3.00")}],
            user=self.user,
            note="Supplier return 3 units",
        )
        self.assertIsNotNone(ret.pk)

        sup_alloc = StockAllocation.objects.get(
            supplier_return_item__stock_return=ret,
            movement_type=StockAllocation.MovementType.SUPPLIER_RETURN,
        )
        self.assertEqual(sup_alloc.quantity, Decimal("3.00"))
        self.assertEqual(sup_alloc.direction, StockAllocation.Direction.OUT)

        self.assert_stock_invariant(self.store1.id, self.product1.id)
        batch = ProductBatch.objects.get(store=self.store1, product=self.product1)
        self.assertEqual(batch.quantity, Decimal("5.00"))

    # =========================================================================
    # 8. FULL SEQUENTIAL LIFECYCLE END-TO-END
    # =========================================================================
    def test_full_lifecycle_sequential_end_to_end(self):
        """
        Executes a complete business sequence:
        StockEntry -> Sale -> SaleReturn -> Transfer -> WriteOff -> Inventory Finalize.
        Verifies assert_stock_invariant after EVERY single mutation.
        """
        # 1. Purchase 15 units @ 100
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store1,
            user=self.user,
            items=[{
                "product": self.product1,
                "quantity": Decimal("15.00"),
                "purchase_price": Decimal("100.00"),
                "selling_price": Decimal("150.00"),
                "wholesale_price": Decimal("130.00"),
            }],
            cash_amount=Decimal("1500.00"),
        )
        self.assert_stock_invariant(self.store1.id, self.product1.id, "After StockEntry")

        # 2. Sell 5 units (15 -> 10)
        sale = SaleService.create_sale(
            user=self.user,
            data={
                "store": self.store1.id,
                "customer": self.customer.id,
                "items": [{
                    "product": self.product1.id,
                    "quantity": Decimal("5.00"),
                    "price": Decimal("150.00"),
                }],
                "payments": [{"type": "cash", "amount": Decimal("750.00")}],
            },
        )
        self.assert_stock_invariant(self.store1.id, self.product1.id, "After Sale")

        # 3. Return 2 units (10 -> 12)
        sale_item = sale.items.get()
        SaleReturnService.create_return(
            user=self.user,
            data={
                "sale": sale.id,
                "items": [{"sale_item": sale_item.id, "quantity": Decimal("2.00")}],
            },
        )
        self.assert_stock_invariant(self.store1.id, self.product1.id, "After SaleReturn")

        # 4. Transfer 4 units to store2 (store1: 12 -> 8, store2: 0 -> 4)
        transfer = TransferService.create_transfer(
            from_store=self.store1,
            to_store=self.store2,
            items_data=[{"product": self.product1, "quantity": Decimal("4.00")}],
            user=self.user,
        )
        TransferService.approve_transfer(transfer_id=transfer.id, user=self.user)
        self.assert_stock_invariant(self.store1.id, self.product1.id, "After Transfer OUT")
        self.assert_stock_invariant(self.store2.id, self.product1.id, "After Transfer IN")

        # 5. Write off 1 unit in store1 (8 -> 7)
        WriteOffService.create_write_off(
            store=self.store1,
            items=[{"product": self.product1, "quantity": Decimal("1.00")}],
            reason=WriteOff.Reason.EXPIRED,
            user=self.user,
        )
        self.assert_stock_invariant(self.store1.id, self.product1.id, "After WriteOff")

        # 6. Inventory finalize in store1 (expected 7, counted 6 -> shortage 1; 7 -> 6)
        session = InventoryService.start_session(user=self.user, store_id=self.store1.id)
        InventoryService.set_count(
            session_id=session.id,
            product_id=self.product1.id,
            quantity=Decimal("6.00"),
        )
        InventoryService.finalize(session_id=session.id)
        self.assert_stock_invariant(self.store1.id, self.product1.id, "After Inventory Shortage")

        batch1 = ProductBatch.objects.get(store=self.store1, product=self.product1)
        batch2 = ProductBatch.objects.get(store=self.store2, product=self.product1)
        self.assertEqual(batch1.quantity, Decimal("6.00"))
        self.assertEqual(batch2.quantity, Decimal("4.00"))
