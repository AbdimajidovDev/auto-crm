from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.test import TestCase
from django.utils import timezone

from apps.contract.models import StockEntry, StockEntryItem, StockEntryReturn, StockEntryReturnItem, Supplier
from apps.inventory.exceptions import (
    AllocationConflictError,
    InsufficientStockError,
    InvalidAllocationError,
    InvalidReversalError,
)
from apps.inventory.models import InventorySession, StockAllocation, StockLot
from apps.inventory.services import InventoryExcessResult, StockAllocationService
from apps.products.models import Product, ProductBatch
from apps.products.utils.barcode_utility import normalize_barcode
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.users.models import User
from apps.writeoff.models import WriteOff, WriteOffItem


class StockAllocationServiceTestBase(TestCase):
    """Shared fixture setup for StockAllocationService unit and integration tests."""

    _barcode_seq = 4000

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(phone_number="998901112233", full_name="Service Test User")
        cls.store_a = Store.objects.create(
            name="Store A (Base)", phone_number="998901111111", address="Alpha", type=Store.StoreType.BASE
        )
        cls.store_b = Store.objects.create(
            name="Store B (Retail)", phone_number="998902222222", address="Beta", type=Store.StoreType.STORE
        )
        cls.supplier = Supplier.objects.create(
            name="Supplier Alpha", phone_number="998903333333", address="Supplier Road"
        )

    def create_product(self, name="Test Product"):
        StockAllocationServiceTestBase._barcode_seq += 1
        return Product.objects.create(
            name=name,
            barcode=normalize_barcode(f"{StockAllocationServiceTestBase._barcode_seq:012d}"),
        )

    def create_lot(
        self,
        store=None,
        product=None,
        supplier=None,
        lot_type=StockLot.LotType.PURCHASE,
        initial_quantity=Decimal("10.00"),
        remaining_quantity=None,
        purchase_price=Decimal("15.00"),
        source_lot=None,
    ):
        st = store or self.store_a
        pr = product or self.product
        rem_qty = initial_quantity if remaining_quantity is None else remaining_quantity
        lot = StockLot.objects.create(
            store=st,
            product=pr,
            supplier=supplier if supplier is not None else self.supplier,
            lot_type=lot_type,
            initial_quantity=initial_quantity,
            remaining_quantity=rem_qty,
            purchase_price=purchase_price,
            source_lot=source_lot,
        )
        # Keep ProductBatch cache synchronized with new lot
        with transaction.atomic():
            StockAllocationService._sync_product_batch(st, pr)
        return lot

    def create_sale_item(self, product=None, store=None, quantity=Decimal("2.00"), unit_price=Decimal("100.00")):
        st = store or self.store_a
        pr = product or self.product
        sale = Sale.objects.create(
            store=st,
            seller=self.user,
            total_amount=quantity * unit_price,
            paid_amount=quantity * unit_price,
            status=Sale.Status.PAID,
        )
        return SaleItem.objects.create(
            sale=sale,
            product=pr,
            quantity=quantity,
            unit_price=unit_price,
            total_price=quantity * unit_price,
        )

    def create_sale_return_item(self, sale_item, quantity=Decimal("1.00")):
        sale_return = SaleReturn.objects.create(
            sale=sale_item.sale,
            store=sale_item.sale.store,
            seller=self.user,
            total_refund=quantity * sale_item.unit_price,
        )
        return SaleReturnItem.objects.create(
            sale_return=sale_return,
            sale_item=sale_item,
            product=sale_item.product,
            quantity=quantity,
            unit_price=sale_item.unit_price,
            total_price=quantity * sale_item.unit_price,
        )

    def create_write_off_item(self, product=None, store=None, quantity=Decimal("1.00")):
        st = store or self.store_a
        pr = product or self.product
        wo = WriteOff.objects.create(
            store=st,
            created_by=self.user,
            reason=WriteOff.Reason.DAMAGED,
        )
        return WriteOffItem.objects.create(
            write_off=wo,
            product=pr,
            quantity=quantity,
            purchase_price=Decimal("15.00"),
            selling_price=Decimal("50.00"),
        )

    def create_transfer_item(self, product=None, from_store=None, to_store=None, quantity=Decimal("2.00")):
        f_store = from_store or self.store_a
        t_store = to_store or self.store_b
        pr = product or self.product
        transfer = StockTransfer.objects.create(
            from_store=f_store,
            to_store=t_store,
            status=StockTransfer.Status.APPROVED,
            created_by=self.user,
        )
        return StockTransferItem.objects.create(
            stock_transfer=transfer,
            product=pr,
            quantity=quantity,
            purchase_price=Decimal("15.00"),
            selling_price=Decimal("50.00"),
        )

    def create_supplier_return_item(self, product=None, store=None, supplier=None, quantity=Decimal("2.00")):
        st = store or self.store_a
        pr = product or self.product
        sup = supplier or self.supplier
        entry = StockEntry.objects.create(
            supplier=sup,
            store=st,
            total_amount=Decimal("100.00"),
            cash_amount=Decimal("100.00"),
            created_by=self.user,
        )
        entry_item = StockEntryItem.objects.create(
            entry=entry,
            product=pr,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("15.00"),
            selling_price=Decimal("50.00"),
        )
        ret = StockEntryReturn.objects.create(
            entry=entry,
            total_amount=quantity * Decimal("15.00"),
            created_by=self.user,
        )
        return StockEntryReturnItem.objects.create(
            stock_return=ret,
            entry_item=entry_item,
            product=pr,
            quantity=quantity,
            purchase_price=Decimal("15.00"),
            amount=quantity * Decimal("15.00"),
        )

    def setUp(self):
        self.product = self.create_product("Standard Widget")


class StockAllocationServiceFifoTests(StockAllocationServiceTestBase):
    """Tests 1-7: FIFO Sale Allocation & ordering behavior."""

    def test_1_single_lot_sale(self):
        lot = self.create_lot(initial_quantity=Decimal("10.00"), purchase_price=Decimal("50.00"))
        sale_item = self.create_sale_item(quantity=Decimal("4.00"))

        allocations = StockAllocationService.allocate_sale(sale_item=sale_item)

        self.assertEqual(len(allocations), 1)
        alloc = allocations[0]
        self.assertEqual(alloc.lot, lot)
        self.assertEqual(alloc.quantity, Decimal("4.00"))
        self.assertEqual(alloc.unit_cost, Decimal("50.00"))
        self.assertEqual(alloc.movement_type, StockAllocation.MovementType.SALE)
        self.assertEqual(alloc.direction, StockAllocation.Direction.OUT)
        self.assertEqual(alloc.sale_item, sale_item)

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("6.00"))

        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("6.00"))

    def test_2_multi_lot_sale(self):
        lot_a = self.create_lot(initial_quantity=Decimal("5.00"), purchase_price=Decimal("100.00"))
        lot_b = self.create_lot(initial_quantity=Decimal("7.00"), purchase_price=Decimal("120.00"))
        sale_item = self.create_sale_item(quantity=Decimal("8.00"))

        allocations = StockAllocationService.allocate_sale(sale_item=sale_item)

        self.assertEqual(len(allocations), 2)
        self.assertEqual(allocations[0].lot, lot_a)
        self.assertEqual(allocations[0].quantity, Decimal("5.00"))
        self.assertEqual(allocations[0].unit_cost, Decimal("100.00"))

        self.assertEqual(allocations[1].lot, lot_b)
        self.assertEqual(allocations[1].quantity, Decimal("3.00"))
        self.assertEqual(allocations[1].unit_cost, Decimal("120.00"))

        lot_a.refresh_from_db()
        lot_b.refresh_from_db()
        self.assertEqual(lot_a.remaining_quantity, Decimal("0.00"))
        self.assertEqual(lot_b.remaining_quantity, Decimal("4.00"))

        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("4.00"))

    def test_3_exact_lot_exhaustion(self):
        lot = self.create_lot(initial_quantity=Decimal("5.00"))
        sale_item = self.create_sale_item(quantity=Decimal("5.00"))

        allocations = StockAllocationService.allocate_sale(sale_item=sale_item)
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].quantity, Decimal("5.00"))

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("0.00"))

        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("0.00"))

    def test_4_partial_lot_consumption(self):
        lot = self.create_lot(initial_quantity=Decimal("10.00"))
        sale_item = self.create_sale_item(quantity=Decimal("3.50"))

        allocations = StockAllocationService.allocate_sale(sale_item=sale_item)
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].quantity, Decimal("3.50"))

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("6.50"))

    def test_5_insufficient_stock_rollback(self):
        lot = self.create_lot(initial_quantity=Decimal("5.00"))
        sale_item = self.create_sale_item(quantity=Decimal("10.00"))

        with self.assertRaises(InsufficientStockError):
            StockAllocationService.allocate_sale(sale_item=sale_item)

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("5.00"))

        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("5.00"))
        self.assertEqual(StockAllocation.objects.count(), 0)

    def test_6_fifo_created_at(self):
        now = timezone.now()
        lot_old = self.create_lot(initial_quantity=Decimal("5.00"), purchase_price=Decimal("10.00"))
        lot_new = self.create_lot(initial_quantity=Decimal("5.00"), purchase_price=Decimal("20.00"))

        # Explicitly backdate lot_old
        StockLot.objects.filter(pk=lot_old.pk).update(created_at=now - timedelta(days=2))
        StockLot.objects.filter(pk=lot_new.pk).update(created_at=now)

        sale_item = self.create_sale_item(quantity=Decimal("3.00"))
        allocations = StockAllocationService.allocate_sale(sale_item=sale_item)

        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].lot_id, lot_old.pk)
        self.assertEqual(allocations[0].unit_cost, Decimal("10.00"))

    def test_7_fifo_id_tie_breaker(self):
        same_time = timezone.now()
        lot_first = self.create_lot(initial_quantity=Decimal("5.00"), purchase_price=Decimal("10.00"))
        lot_second = self.create_lot(initial_quantity=Decimal("5.00"), purchase_price=Decimal("20.00"))

        StockLot.objects.filter(pk__in=[lot_first.pk, lot_second.pk]).update(created_at=same_time)

        sale_item = self.create_sale_item(quantity=Decimal("3.00"))
        allocations = StockAllocationService.allocate_sale(sale_item=sale_item)

        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].lot_id, lot_first.pk)


class StockAllocationServiceReturnTests(StockAllocationServiceTestBase):
    """Tests 8-15: Reverse LIFO Sale Return & Invariants."""

    def test_8_single_allocation_return(self):
        lot = self.create_lot(initial_quantity=Decimal("10.00"), purchase_price=Decimal("50.00"))
        sale_item = self.create_sale_item(quantity=Decimal("4.00"))
        sale_allocs = StockAllocationService.allocate_sale(sale_item=sale_item)

        return_item = self.create_sale_return_item(sale_item=sale_item, quantity=Decimal("2.00"))
        return_allocs = StockAllocationService.reverse_sale_return(sale_return_item=return_item)

        self.assertEqual(len(return_allocs), 1)
        ret_alloc = return_allocs[0]
        self.assertEqual(ret_alloc.lot, lot)
        self.assertEqual(ret_alloc.quantity, Decimal("2.00"))
        self.assertEqual(ret_alloc.movement_type, StockAllocation.MovementType.SALE_RETURN)
        self.assertEqual(ret_alloc.direction, StockAllocation.Direction.IN)
        self.assertEqual(ret_alloc.reversal_of, sale_allocs[0])
        self.assertEqual(ret_alloc.sale_return_item, return_item)

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("8.00"))

        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("8.00"))

    def test_9_multi_allocation_reverse_lifo(self):
        now = timezone.now()
        lot_a = self.create_lot(initial_quantity=Decimal("5.00"), purchase_price=Decimal("100.00"))
        lot_b = self.create_lot(initial_quantity=Decimal("7.00"), purchase_price=Decimal("120.00"))
        StockLot.objects.filter(pk=lot_a.pk).update(created_at=now - timedelta(hours=2))
        StockLot.objects.filter(pk=lot_b.pk).update(created_at=now)

        sale_item = self.create_sale_item(quantity=Decimal("8.00"))
        sale_allocs = StockAllocationService.allocate_sale(sale_item=sale_item)
        # sale_allocs[0]: Lot A (5), sale_allocs[1]: Lot B (3)

        return_item = self.create_sale_return_item(sale_item=sale_item, quantity=Decimal("4.00"))
        return_allocs = StockAllocationService.reverse_sale_return(sale_return_item=return_item)

        # Reverse LIFO: must reverse Lot B (3) first, then Lot A (1)
        self.assertEqual(len(return_allocs), 2)
        self.assertEqual(return_allocs[0].lot, lot_b)
        self.assertEqual(return_allocs[0].quantity, Decimal("3.00"))
        self.assertEqual(return_allocs[0].reversal_of, sale_allocs[1])

        self.assertEqual(return_allocs[1].lot, lot_a)
        self.assertEqual(return_allocs[1].quantity, Decimal("1.00"))
        self.assertEqual(return_allocs[1].reversal_of, sale_allocs[0])

        lot_a.refresh_from_db()
        lot_b.refresh_from_db()
        self.assertEqual(lot_a.remaining_quantity, Decimal("1.00"))
        self.assertEqual(lot_b.remaining_quantity, Decimal("7.00"))

        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("8.00"))

    def test_10_partial_reversal(self):
        self.create_lot(initial_quantity=Decimal("10.00"))
        sale_item = self.create_sale_item(quantity=Decimal("5.00"))
        StockAllocationService.allocate_sale(sale_item=sale_item)

        ret_item_1 = self.create_sale_return_item(sale_item=sale_item, quantity=Decimal("2.00"))
        StockAllocationService.reverse_sale_return(sale_return_item=ret_item_1)

        ret_item_2 = self.create_sale_return_item(sale_item=sale_item, quantity=Decimal("2.00"))
        StockAllocationService.reverse_sale_return(sale_return_item=ret_item_2)

        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("9.00"))

    def test_11_full_reversal(self):
        lot = self.create_lot(initial_quantity=Decimal("5.00"))
        sale_item = self.create_sale_item(quantity=Decimal("5.00"))
        StockAllocationService.allocate_sale(sale_item=sale_item)

        return_item = self.create_sale_return_item(sale_item=sale_item, quantity=Decimal("5.00"))
        StockAllocationService.reverse_sale_return(sale_return_item=return_item)

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("5.00"))
        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("5.00"))

    def test_12_over_return_rejected(self):
        self.create_lot(initial_quantity=Decimal("5.00"))
        sale_item = self.create_sale_item(quantity=Decimal("4.00"))
        StockAllocationService.allocate_sale(sale_item=sale_item)

        return_item = self.create_sale_return_item(sale_item=sale_item, quantity=Decimal("5.00"))
        with self.assertRaises(InvalidReversalError):
            StockAllocationService.reverse_sale_return(sale_return_item=return_item)

    def test_13_duplicate_return_protection(self):
        self.create_lot(initial_quantity=Decimal("10.00"))
        sale_item = self.create_sale_item(quantity=Decimal("4.00"))
        StockAllocationService.allocate_sale(sale_item=sale_item)

        return_item = self.create_sale_return_item(sale_item=sale_item, quantity=Decimal("2.00"))
        StockAllocationService.reverse_sale_return(sale_return_item=return_item)

        # Calling again with same return_item must raise conflict
        with self.assertRaises(AllocationConflictError):
            StockAllocationService.reverse_sale_return(sale_return_item=return_item)

    def test_14_reversal_of_correctly_populated(self):
        self.create_lot(initial_quantity=Decimal("5.00"))
        sale_item = self.create_sale_item(quantity=Decimal("2.00"))
        sale_allocs = StockAllocationService.allocate_sale(sale_item=sale_item)

        return_item = self.create_sale_return_item(sale_item=sale_item, quantity=Decimal("2.00"))
        ret_allocs = StockAllocationService.reverse_sale_return(sale_return_item=return_item)

        self.assertIsNotNone(ret_allocs[0].reversal_of)
        self.assertEqual(ret_allocs[0].reversal_of_id, sale_allocs[0].id)

    def test_15_same_lot_guaranteed(self):
        lot = self.create_lot(initial_quantity=Decimal("5.00"))
        sale_item = self.create_sale_item(quantity=Decimal("2.00"))
        sale_allocs = StockAllocationService.allocate_sale(sale_item=sale_item)

        return_item = self.create_sale_return_item(sale_item=sale_item, quantity=Decimal("2.00"))
        ret_allocs = StockAllocationService.reverse_sale_return(sale_return_item=return_item)

        self.assertEqual(ret_allocs[0].lot, lot)
        self.assertEqual(ret_allocs[0].lot, sale_allocs[0].lot)


class StockAllocationServiceWriteOffAndSupplierTests(StockAllocationServiceTestBase):
    """Tests 16-20: Write-Off and Supplier Return operations."""

    def test_16_single_lot_write_off(self):
        lot = self.create_lot(initial_quantity=Decimal("10.00"), purchase_price=Decimal("15.00"))
        wo_item = self.create_write_off_item(quantity=Decimal("3.00"))

        allocs = StockAllocationService.allocate_write_off(write_off_item=wo_item)
        self.assertEqual(len(allocs), 1)
        self.assertEqual(allocs[0].quantity, Decimal("3.00"))
        self.assertEqual(allocs[0].movement_type, StockAllocation.MovementType.WRITE_OFF)
        self.assertEqual(allocs[0].direction, StockAllocation.Direction.OUT)
        self.assertEqual(allocs[0].write_off_item, wo_item)

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("7.00"))
        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("7.00"))

    def test_17_multi_lot_fifo_write_off(self):
        lot_1 = self.create_lot(initial_quantity=Decimal("5.00"))
        lot_2 = self.create_lot(initial_quantity=Decimal("5.00"))
        wo_item = self.create_write_off_item(quantity=Decimal("7.00"))

        allocs = StockAllocationService.allocate_write_off(write_off_item=wo_item)
        self.assertEqual(len(allocs), 2)
        self.assertEqual(allocs[0].lot, lot_1)
        self.assertEqual(allocs[0].quantity, Decimal("5.00"))
        self.assertEqual(allocs[1].lot, lot_2)
        self.assertEqual(allocs[1].quantity, Decimal("2.00"))

        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("3.00"))

    def test_18_write_off_insufficient_stock_rollback(self):
        self.create_lot(initial_quantity=Decimal("5.00"))
        wo_item = self.create_write_off_item(quantity=Decimal("10.00"))

        with self.assertRaises(InsufficientStockError):
            StockAllocationService.allocate_write_off(write_off_item=wo_item)

        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("5.00"))

    def test_19_fifo_supplier_return(self):
        lot = self.create_lot(initial_quantity=Decimal("10.00"), purchase_price=Decimal("15.00"))
        sup_ret_item = self.create_supplier_return_item(quantity=Decimal("4.00"))

        allocs = StockAllocationService.allocate_supplier_return(supplier_return_item=sup_ret_item)
        self.assertEqual(len(allocs), 1)
        self.assertEqual(allocs[0].quantity, Decimal("4.00"))
        self.assertEqual(allocs[0].movement_type, StockAllocation.MovementType.SUPPLIER_RETURN)
        self.assertEqual(allocs[0].direction, StockAllocation.Direction.OUT)
        self.assertEqual(allocs[0].supplier_return_item, sup_ret_item)

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("6.00"))

    def test_20_supplier_lineage_preserved(self):
        other_supplier = Supplier.objects.create(name="Other Supplier", phone_number="998909998877")
        lot_other = self.create_lot(supplier=other_supplier, initial_quantity=Decimal("5.00"))
        lot_main = self.create_lot(supplier=self.supplier, initial_quantity=Decimal("5.00"))

        sup_ret_item = self.create_supplier_return_item(supplier=self.supplier, quantity=Decimal("3.00"))
        allocs = StockAllocationService.allocate_supplier_return(supplier_return_item=sup_ret_item)

        self.assertEqual(len(allocs), 1)
        self.assertEqual(allocs[0].lot, lot_main)
        self.assertEqual(allocs[0].lot.supplier, self.supplier)


class StockAllocationServiceTransferTests(StockAllocationServiceTestBase):
    """Tests 21-26: Multi-lot Transfer Out and Transfer In Lineage."""

    def test_21_multi_lot_transfer_out(self):
        lot_1 = self.create_lot(initial_quantity=Decimal("3.00"))
        lot_2 = self.create_lot(initial_quantity=Decimal("5.00"))
        transfer_item = self.create_transfer_item(quantity=Decimal("5.00"))

        allocs = StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        self.assertEqual(len(allocs), 2)
        self.assertEqual(allocs[0].lot, lot_1)
        self.assertEqual(allocs[0].quantity, Decimal("3.00"))
        self.assertEqual(allocs[1].lot, lot_2)
        self.assertEqual(allocs[1].quantity, Decimal("2.00"))

        batch_a = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch_a.quantity, Decimal("3.00"))

    def test_22_transfer_in_creates_correct_lots(self):
        lot_1 = self.create_lot(initial_quantity=Decimal("3.00"), purchase_price=Decimal("20.00"))
        lot_2 = self.create_lot(initial_quantity=Decimal("4.00"), purchase_price=Decimal("25.00"))
        transfer_item = self.create_transfer_item(quantity=Decimal("5.00"))

        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        self.assertEqual(len(in_allocs), 2)
        dest_lot_1 = in_allocs[0].lot
        dest_lot_2 = in_allocs[1].lot

        self.assertEqual(dest_lot_1.store, self.store_b)
        self.assertEqual(dest_lot_1.lot_type, StockLot.LotType.TRANSFER_IN)
        self.assertEqual(dest_lot_1.remaining_quantity, Decimal("3.00"))

        self.assertEqual(dest_lot_2.store, self.store_b)
        self.assertEqual(dest_lot_2.remaining_quantity, Decimal("2.00"))

    def test_23_transfer_in_source_lot_preserved(self):
        lot_src = self.create_lot(initial_quantity=Decimal("5.00"))
        transfer_item = self.create_transfer_item(quantity=Decimal("3.00"))

        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        self.assertEqual(in_allocs[0].lot.source_lot, lot_src)

    def test_24_transfer_in_supplier_preserved(self):
        lot_src = self.create_lot(initial_quantity=Decimal("5.00"), supplier=self.supplier)
        transfer_item = self.create_transfer_item(quantity=Decimal("3.00"))

        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        self.assertEqual(in_allocs[0].lot.supplier, self.supplier)

    def test_25_transfer_in_purchase_price_preserved(self):
        lot_src = self.create_lot(initial_quantity=Decimal("5.00"), purchase_price=Decimal("77.50"))
        transfer_item = self.create_transfer_item(quantity=Decimal("3.00"))

        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        self.assertEqual(in_allocs[0].lot.purchase_price, Decimal("77.50"))
        self.assertEqual(in_allocs[0].unit_cost, Decimal("77.50"))

    def test_26_transfer_in_no_double_counting(self):
        self.create_lot(initial_quantity=Decimal("10.00"))
        transfer_item = self.create_transfer_item(quantity=Decimal("4.00"))

        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        # Destination store stock must be strictly 4.00
        batch_b = ProductBatch.objects.get(store=self.store_b, product=self.product)
        self.assertEqual(batch_b.quantity, Decimal("4.00"))
        self.assertEqual(
            StockLot.objects.filter(store=self.store_b, product=self.product).aggregate(
                s=Decimal(0) or models.Sum("remaining_quantity")
            )["s"],
            Decimal("4.00"),
        )


class StockAllocationServiceInventoryTests(StockAllocationServiceTestBase):
    """Tests 27-30: Inventory Shortage and Excess."""

    def setUp(self):
        super().setUp()
        self.session = InventorySession.objects.create(store=self.store_a, started_by=self.user)

    def test_27_shortage_fifo(self):
        lot = self.create_lot(initial_quantity=Decimal("10.00"))
        allocs = StockAllocationService.allocate_inventory_shortage(
            inventory_session=self.session,
            product=self.product,
            quantity=Decimal("3.00"),
        )
        self.assertEqual(len(allocs), 1)
        self.assertEqual(allocs[0].movement_type, StockAllocation.MovementType.INVENTORY_SHORTAGE)
        self.assertEqual(allocs[0].direction, StockAllocation.Direction.OUT)
        self.assertEqual(allocs[0].quantity, Decimal("3.00"))

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("7.00"))

    def test_28_shortage_linked_to_inventory_session(self):
        self.create_lot(initial_quantity=Decimal("5.00"))
        allocs = StockAllocationService.allocate_inventory_shortage(
            inventory_session=self.session,
            product=self.product,
            quantity=Decimal("2.00"),
        )
        alloc = allocs[0]
        self.assertEqual(alloc.inventory_session, self.session)
        self.assertIsNone(alloc.sale_item)
        self.assertIsNone(alloc.sale_return_item)
        self.assertIsNone(alloc.write_off_item)
        self.assertIsNone(alloc.transfer_item)
        self.assertIsNone(alloc.supplier_return_item)

    def test_29_excess_creates_new_lot(self):
        res = StockAllocationService.create_inventory_excess_lot(
            inventory_session=self.session,
            product=self.product,
            quantity=Decimal("6.00"),
            purchase_price=Decimal("45.00"),
        )
        self.assertIsInstance(res, InventoryExcessResult)
        lot = res.lot
        self.assertEqual(lot.lot_type, StockLot.LotType.INVENTORY_EXCESS)
        self.assertEqual(lot.initial_quantity, Decimal("6.00"))
        self.assertEqual(lot.remaining_quantity, Decimal("6.00"))
        self.assertEqual(lot.purchase_price, Decimal("45.00"))

    def test_30_excess_linked_to_inventory_session(self):
        res = StockAllocationService.create_inventory_excess_lot(
            inventory_session=self.session,
            product=self.product,
            quantity=Decimal("4.00"),
        )
        alloc = res.allocation
        self.assertEqual(alloc.inventory_session, self.session)
        self.assertEqual(alloc.movement_type, StockAllocation.MovementType.INVENTORY_EXCESS)
        self.assertEqual(alloc.direction, StockAllocation.Direction.IN)
        self.assertIsNone(alloc.sale_item)


class StockAllocationServiceInvariantTests(StockAllocationServiceTestBase):
    """Tests 31-37: ProductBatch.quantity == SUM(StockLot.remaining_quantity) Invariant."""

    def _assert_product_batch_invariant(self, store, product):
        batch = ProductBatch.objects.get(store=store, product=product)
        lots_sum = (
            StockLot.objects.filter(store=store, product=product).aggregate(
                s=models.Sum("remaining_quantity")
            )["s"]
            or Decimal("0.00")
        )
        self.assertEqual(batch.quantity, lots_sum)

    def test_31_sale_keeps_invariant(self):
        self.create_lot(initial_quantity=Decimal("12.00"))
        sale_item = self.create_sale_item(quantity=Decimal("5.00"))
        StockAllocationService.allocate_sale(sale_item=sale_item)
        self._assert_product_batch_invariant(self.store_a, self.product)

    def test_32_return_keeps_invariant(self):
        self.create_lot(initial_quantity=Decimal("10.00"))
        sale_item = self.create_sale_item(quantity=Decimal("4.00"))
        StockAllocationService.allocate_sale(sale_item=sale_item)
        return_item = self.create_sale_return_item(sale_item=sale_item, quantity=Decimal("2.00"))
        StockAllocationService.reverse_sale_return(sale_return_item=return_item)
        self._assert_product_batch_invariant(self.store_a, self.product)

    def test_33_write_off_keeps_invariant(self):
        self.create_lot(initial_quantity=Decimal("10.00"))
        wo_item = self.create_write_off_item(quantity=Decimal("4.00"))
        StockAllocationService.allocate_write_off(write_off_item=wo_item)
        self._assert_product_batch_invariant(self.store_a, self.product)

    def test_34_supplier_return_keeps_invariant(self):
        self.create_lot(initial_quantity=Decimal("10.00"))
        sup_ret_item = self.create_supplier_return_item(quantity=Decimal("3.00"))
        StockAllocationService.allocate_supplier_return(supplier_return_item=sup_ret_item)
        self._assert_product_batch_invariant(self.store_a, self.product)

    def test_35_transfer_keeps_source_destination_invariant(self):
        self.create_lot(initial_quantity=Decimal("10.00"))
        transfer_item = self.create_transfer_item(quantity=Decimal("4.00"))
        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        self._assert_product_batch_invariant(self.store_a, self.product)

        StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)
        self._assert_product_batch_invariant(self.store_b, self.product)

    def test_36_inventory_shortage_keeps_invariant(self):
        session = InventorySession.objects.create(store=self.store_a, started_by=self.user)
        self.create_lot(initial_quantity=Decimal("10.00"))
        StockAllocationService.allocate_inventory_shortage(
            inventory_session=session, product=self.product, quantity=Decimal("3.00")
        )
        self._assert_product_batch_invariant(self.store_a, self.product)

    def test_37_inventory_excess_keeps_invariant(self):
        session = InventorySession.objects.create(store=self.store_a, started_by=self.user)
        StockAllocationService.create_inventory_excess_lot(
            inventory_session=session, product=self.product, quantity=Decimal("7.00")
        )
        self._assert_product_batch_invariant(self.store_a, self.product)


class StockAllocationServiceTransactionAndConcurrencyTests(StockAllocationServiceTestBase):
    """Tests 38-44: Transactions, Rollback, Deterministic Locking, Immutability & Performance."""

    def test_38_rollback_leaves_product_batch_and_stock_lot_unchanged(self):
        lot = self.create_lot(initial_quantity=Decimal("5.00"))
        sale_item = self.create_sale_item(quantity=Decimal("10.00"))

        try:
            with transaction.atomic():
                StockAllocationService.allocate_sale(sale_item=sale_item)
        except InsufficientStockError:
            pass

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("5.00"))
        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("5.00"))

    def test_39_rollback_leaves_no_partial_allocations(self):
        self.create_lot(initial_quantity=Decimal("5.00"))
        sale_item = self.create_sale_item(quantity=Decimal("10.00"))

        with self.assertRaises(InsufficientStockError):
            StockAllocationService.allocate_sale(sale_item=sale_item)

        self.assertEqual(StockAllocation.objects.count(), 0)

    def test_40_concurrent_allocation_cannot_oversell(self):
        self.create_lot(initial_quantity=Decimal("10.00"))
        sale_item_1 = self.create_sale_item(quantity=Decimal("6.00"))
        sale_item_2 = self.create_sale_item(quantity=Decimal("6.00"))

        StockAllocationService.allocate_sale(sale_item=sale_item_1)
        with self.assertRaises(InsufficientStockError):
            StockAllocationService.allocate_sale(sale_item=sale_item_2)

        batch = ProductBatch.objects.get(store=self.store_a, product=self.product)
        self.assertEqual(batch.quantity, Decimal("4.00"))

    def test_41_deterministic_locking_behavior(self):
        prod_2 = self.create_product("Another Product")
        prod_3 = self.create_product("Zebra Product")
        # Ensure unsorted order is handled deterministically
        pids = [prod_3.id, self.product.id, prod_2.id]
        with transaction.atomic():
            batches, lots = StockAllocationService._lock_batches_and_lots(self.store_a, pids)
            # Keys must match sorted IDs
            self.assertEqual(list(batches.keys()), sorted(pids))

    def test_42_immutability_allocation_update_rejected(self):
        self.create_lot(initial_quantity=Decimal("10.00"))
        sale_item = self.create_sale_item(quantity=Decimal("2.00"))
        allocs = StockAllocationService.allocate_sale(sale_item=sale_item)

        alloc = allocs[0]
        alloc.quantity = Decimal("99.00")
        with self.assertRaises(ValidationError):
            alloc.save()

    def test_43_immutability_allocation_delete_rejected(self):
        self.create_lot(initial_quantity=Decimal("10.00"))
        sale_item = self.create_sale_item(quantity=Decimal("2.00"))
        allocs = StockAllocationService.allocate_sale(sale_item=sale_item)

        alloc = allocs[0]
        with self.assertRaises(ValidationError):
            alloc.delete()

    def test_44_query_count_bounded(self):
        self.create_lot(initial_quantity=Decimal("10.00"))
        sale_item = self.create_sale_item(quantity=Decimal("3.00"))

        with self.assertNumQueries(11):
            # Expect bounded queries (including transaction savepoint and synchronization)
            StockAllocationService.allocate_sale(sale_item=sale_item)


class StockLedgerSemanticsVerificationTests(StockAllocationServiceTestBase):
    """
    Step 2.1: Rigorous verification of Stock Ledger Semantics (Variant A).
    Ensures:
      - StockLot is the authoritative balance state (initial_quantity / remaining_quantity).
      - StockAllocation is an immutable movement/audit ledger.
      - TRANSFER_IN creates a new lot where remaining_quantity == initial_quantity,
        and TRANSFER_IN allocation is purely an audit entry (NO double counting).
      - INVENTORY_EXCESS creates a new lot where remaining_quantity == initial_quantity,
        and INVENTORY_EXCESS allocation is purely an audit entry (NO double counting).
      - ProductBatch.quantity == SUM(StockLot.remaining_quantity) holds after EVERY operation.
    """

    def _assert_batch_invariant(self, store, product):
        batch = ProductBatch.objects.get(store=store, product=product)
        sum_remaining = (
            StockLot.objects.filter(store=store, product=product).aggregate(
                s=models.Sum("remaining_quantity")
            )["s"]
            or Decimal("0.00")
        )
        self.assertEqual(batch.quantity, sum_remaining)
        return batch.quantity

    def test_step_2_1_transfer_in_no_double_counting(self):
        """
        1. Transfer-in:
           source qty = 10
           transfer = 4
           destination lot initial=4, remaining=4
           destination ProductBatch = 4 (NOT 8!)
           allocation exists (TRANSFER_IN, direction=IN, qty=4)
           double count yo'q!
        """
        # Source store A has 10 units
        src_lot = self.create_lot(store=self.store_a, initial_quantity=Decimal("10.00"))
        self._assert_batch_invariant(self.store_a, self.product)
        self.assertEqual(ProductBatch.objects.get(store=self.store_a, product=self.product).quantity, Decimal("10.00"))

        # Destination store B starts at 0 units
        batch_b = ProductBatch.objects.filter(store=self.store_b, product=self.product).first()
        b_initial_qty = batch_b.quantity if batch_b else Decimal("0.00")
        self.assertEqual(b_initial_qty, Decimal("0.00"))

        # Transfer 4 units from store A to store B
        transfer_item = self.create_transfer_item(
            from_store=self.store_a, to_store=self.store_b, quantity=Decimal("4.00")
        )
        out_allocs = StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        self.assertEqual(len(out_allocs), 1)
        self._assert_batch_invariant(self.store_a, self.product)
        self.assertEqual(ProductBatch.objects.get(store=self.store_a, product=self.product).quantity, Decimal("6.00"))

        # Receive at destination store B
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)
        self.assertEqual(len(in_allocs), 1)

        # Invariant checks on destination store B
        dest_lot = in_allocs[0].lot
        self.assertEqual(dest_lot.store, self.store_b)
        self.assertEqual(dest_lot.initial_quantity, Decimal("4.00"))
        self.assertEqual(dest_lot.remaining_quantity, Decimal("4.00"))

        # Ledger allocation exists
        self.assertEqual(in_allocs[0].movement_type, StockAllocation.MovementType.TRANSFER_IN)
        self.assertEqual(in_allocs[0].direction, StockAllocation.Direction.IN)
        self.assertEqual(in_allocs[0].quantity, Decimal("4.00"))

        # CRUCIAL: Destination ProductBatch must be EXACTLY +4.00 (NOT 8.00!)
        dest_batch_qty = self._assert_batch_invariant(self.store_b, self.product)
        self.assertEqual(dest_batch_qty, Decimal("4.00"))

    def test_step_2_1_inventory_excess_no_double_counting(self):
        """
        2. Inventory excess:
           excess = 3
           lot initial = 3, remaining = 3
           ProductBatch = +3 (NOT 6!)
           allocation exists (INVENTORY_EXCESS, direction=IN, qty=3)
           double count yo'q!
        """
        session = InventorySession.objects.create(store=self.store_a, started_by=self.user)
        batch_before = ProductBatch.objects.filter(store=self.store_a, product=self.product).first()
        qty_before = batch_before.quantity if batch_before else Decimal("0.00")

        res = StockAllocationService.create_inventory_excess_lot(
            inventory_session=session,
            product=self.product,
            quantity=Decimal("3.00"),
            purchase_price=Decimal("25.00"),
        )
        lot = res.lot
        alloc = res.allocation

        self.assertEqual(lot.initial_quantity, Decimal("3.00"))
        self.assertEqual(lot.remaining_quantity, Decimal("3.00"))
        self.assertEqual(alloc.movement_type, StockAllocation.MovementType.INVENTORY_EXCESS)
        self.assertEqual(alloc.direction, StockAllocation.Direction.IN)
        self.assertEqual(alloc.quantity, Decimal("3.00"))

        # CRUCIAL: ProductBatch must increase by EXACTLY 3.00 (NOT 6.00!)
        batch_after_qty = self._assert_batch_invariant(self.store_a, self.product)
        self.assertEqual(batch_after_qty, qty_before + Decimal("3.00"))

    def test_step_2_1_sequential_full_lifecycle_and_invariants(self):
        """
        Sequential flow verifying:
        3. Oddiy SALE: lot initial=10, sale=4 -> remaining=6, allocation OUT=4
        4. SALE_RETURN: remaining 6 -> 7, allocation IN=1, reversal_of correct
        5. WRITE_OFF: remaining 7 -> 5, allocation OUT=2
        Along with ProductBatch.quantity == SUM(StockLot.remaining_quantity) verified at every single step!
        """
        # Step 0: Lot initial = 10
        lot = self.create_lot(store=self.store_a, initial_quantity=Decimal("10.00"), purchase_price=Decimal("15.00"))
        self._assert_batch_invariant(self.store_a, self.product)
        self.assertEqual(lot.remaining_quantity, Decimal("10.00"))
        self.assertEqual(ProductBatch.objects.get(store=self.store_a, product=self.product).quantity, Decimal("10.00"))

        # Step 3: Oddiy SALE (qty=4)
        sale_item = self.create_sale_item(quantity=Decimal("4.00"))
        sale_allocs = StockAllocationService.allocate_sale(sale_item=sale_item)
        self.assertEqual(len(sale_allocs), 1)
        self.assertEqual(sale_allocs[0].movement_type, StockAllocation.MovementType.SALE)
        self.assertEqual(sale_allocs[0].direction, StockAllocation.Direction.OUT)
        self.assertEqual(sale_allocs[0].quantity, Decimal("4.00"))

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("6.00"))
        self._assert_batch_invariant(self.store_a, self.product)
        self.assertEqual(ProductBatch.objects.get(store=self.store_a, product=self.product).quantity, Decimal("6.00"))

        # Step 4: SALE_RETURN (qty=1)
        return_item = self.create_sale_return_item(sale_item=sale_item, quantity=Decimal("1.00"))
        return_allocs = StockAllocationService.reverse_sale_return(sale_return_item=return_item)
        self.assertEqual(len(return_allocs), 1)
        self.assertEqual(return_allocs[0].movement_type, StockAllocation.MovementType.SALE_RETURN)
        self.assertEqual(return_allocs[0].direction, StockAllocation.Direction.IN)
        self.assertEqual(return_allocs[0].quantity, Decimal("1.00"))
        self.assertEqual(return_allocs[0].reversal_of, sale_allocs[0])

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("7.00"))
        self._assert_batch_invariant(self.store_a, self.product)
        self.assertEqual(ProductBatch.objects.get(store=self.store_a, product=self.product).quantity, Decimal("7.00"))

        # Step 5: WRITE_OFF (qty=2)
        wo_item = self.create_write_off_item(quantity=Decimal("2.00"))
        wo_allocs = StockAllocationService.allocate_write_off(write_off_item=wo_item)
        self.assertEqual(len(wo_allocs), 1)
        self.assertEqual(wo_allocs[0].movement_type, StockAllocation.MovementType.WRITE_OFF)
        self.assertEqual(wo_allocs[0].direction, StockAllocation.Direction.OUT)
        self.assertEqual(wo_allocs[0].quantity, Decimal("2.00"))

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("5.00"))
        self._assert_batch_invariant(self.store_a, self.product)
        self.assertEqual(ProductBatch.objects.get(store=self.store_a, product=self.product).quantity, Decimal("5.00"))

