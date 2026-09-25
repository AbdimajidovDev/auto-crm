from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from apps.contract.models import StockEntry, StockEntryItem, Supplier
from apps.contract.services.stock_entry_service import StockEntryService
from apps.inventory.exceptions import InsufficientStockError
from apps.inventory.models import StockAllocation, StockLot
from apps.inventory.services.stock_allocation_service import StockAllocationService
from apps.products.models import Product, ProductBatch
from apps.products.utils.barcode_utility import normalize_barcode
from apps.reports.services.supplier_sales_report_service import SupplierSalesReportService
from apps.sales.models import Sale, SaleItem
from apps.sales.services.sales_services import SaleService
from apps.store.models import Store, StoreUser
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.transfer.services.transfer_service import TransferService
from apps.users.models import User


class TransferPriceLineageTests(TestCase):
    """
    Comprehensive test suite verifying StockTransfer price inheritance and lineage:
    1. source new price lot -> destination new lot inherits price
    2. destination old stock price is not overwritten
    3. FIFO transfer takes older lot first
    4. mixed-price transfer creates separate destination lots with their own prices
    5. source_lot lineage and multi-hop transfer
    6. purchase price preservation
    7. selling price resolution
    8. destination sale uses correct price and FIFO cost
    9. ProductBatch synchronization
    10. StockAllocation ledger correctness
    11. rollback on failed transfer
    12. existing transfer regression
    13. Supplier Sales regression
    """

    _barcode_seq = 8000

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            phone_number="998901000001",
            full_name="Super Admin",
            password="secretpassword",
        )
        cls.user = User.objects.create_user(
            phone_number="998901000002",
            full_name="Staff User",
            password="secretpassword",
        )

        cls.store_a = Store.objects.create(
            name="Store A (Warehouse)",
            phone_number="998901110000",
            address="Warehouse A",
            type=Store.StoreType.BASE,
        )
        cls.store_b = Store.objects.create(
            name="Store B (Retail 1)",
            phone_number="998902220000",
            address="Retail B",
            type=Store.StoreType.STORE,
        )
        cls.store_c = Store.objects.create(
            name="Store C (Retail 2)",
            phone_number="998903330000",
            address="Retail C",
            type=Store.StoreType.STORE,
        )

        StoreUser.objects.create(user=cls.superuser, store=cls.store_a, is_active=True)
        StoreUser.objects.create(user=cls.superuser, store=cls.store_b, is_active=True)
        StoreUser.objects.create(user=cls.superuser, store=cls.store_c, is_active=True)
        StoreUser.objects.create(user=cls.user, store=cls.store_a, is_active=True)
        StoreUser.objects.create(user=cls.user, store=cls.store_b, is_active=True)
        StoreUser.objects.create(user=cls.user, store=cls.store_c, is_active=True)

        cls.supplier = Supplier.objects.create(
            name="Global Automotive Supplier",
            phone_number="998909990000",
            address="Industrial Zone",
        )

    def create_product(self, name="Brake Pad"):
        TransferPriceLineageTests._barcode_seq += 1
        return Product.objects.create(
            name=name,
            barcode=normalize_barcode(f"{TransferPriceLineageTests._barcode_seq:012d}"),
        )

    def create_stock_entry_lot(
        self,
        store,
        product,
        quantity,
        purchase_price,
        selling_price,
        wholesale_price=None,
        supplier=None,
    ):
        """Creates a real StockEntry, StockEntryItem, and StockLot with full price lineage."""
        sup = supplier or self.supplier
        entry = StockEntry.objects.create(
            supplier=sup,
            store=store,
            total_amount=quantity * purchase_price,
            cash_amount=quantity * purchase_price,
            created_by=self.superuser,
        )
        item = StockEntryItem.objects.create(
            entry=entry,
            product=product,
            quantity=quantity,
            purchase_price=purchase_price,
            selling_price=selling_price,
            wholesale_price=wholesale_price or (selling_price * Decimal("0.9")),
        )
        lot = StockLot.objects.create(
            store=store,
            product=product,
            supplier=sup,
            stock_entry_item=item,
            lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=quantity,
            remaining_quantity=quantity,
            purchase_price=purchase_price,
        )
        with transaction.atomic():
            StockAllocationService._sync_product_batch(store, product)
        return lot, item

    def create_transfer_item(self, from_store, to_store, product, quantity):
        transfer = StockTransfer.objects.create(
            from_store=from_store,
            to_store=to_store,
            status=StockTransfer.Status.APPROVED,
            created_by=self.superuser,
        )
        return StockTransferItem.objects.create(
            stock_transfer=transfer,
            product=product,
            quantity=quantity,
            purchase_price=Decimal("0.00"),
            selling_price=Decimal("0.00"),
        )

    # 1. source yangi narxdagi lot -> destination yangi lot narxni oladi
    def test_01_source_new_price_lot_transfers_price_to_destination_new_lot(self):
        """
        Scenario:
        Store A has old lot: 10 @ 100 000 / 120 000.
        Store B has 0 stock.
        Store A gets new lot: 10 @ 150 000 / 180 000.
        Store A exhausts old lot (e.g. 10 sold).
        Store A transfers 5 units of new lot to Store B.
        Destination Store B should receive 5 units with:
          purchase_price = 150 000
          selling_price = 180 000
        ProductBatch in Store B should immediately reflect 150 000 / 180 000.
        """
        product = self.create_product("Engine Oil 5W-30")

        # Old lot at Store A
        lot_old, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("120000.00"),
        )

        # New lot at Store A
        lot_new, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("150000.00"),
            selling_price=Decimal("180000.00"),
        )

        # Exhaust old lot via sale
        sale = Sale.objects.create(
            store=self.store_a,
            seller=self.superuser,
            total_amount=Decimal("1200000.00"),
            paid_amount=Decimal("1200000.00"),
            status=Sale.Status.PAID,
        )
        sale_item = SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=Decimal("10.00"),
            unit_price=Decimal("120000.00"),
            purchase_price=Decimal("100000.00"),
            total_price=Decimal("1200000.00"),
        )
        StockAllocationService.allocate_sale(sale_item=sale_item)
        lot_old.refresh_from_db()
        self.assertEqual(lot_old.remaining_quantity, Decimal("0.00"))

        # Now active lot at Store A is lot_new (150 000 / 180 000)
        transfer_item = self.create_transfer_item(
            from_store=self.store_a,
            to_store=self.store_b,
            product=product,
            quantity=Decimal("5.00"),
        )
        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        self.assertEqual(len(in_allocs), 1)
        dest_lot = in_allocs[0].lot

        # Assert destination lot has inherited the new prices and lineage
        self.assertEqual(dest_lot.store, self.store_b)
        self.assertEqual(dest_lot.remaining_quantity, Decimal("5.00"))
        self.assertEqual(dest_lot.purchase_price, Decimal("150000.00"))
        self.assertEqual(dest_lot.selling_price, Decimal("180000.00"))
        self.assertEqual(dest_lot.source_lot, lot_new)
        self.assertEqual(dest_lot.supplier, self.supplier)

        # Assert destination ProductBatch cache has the new prices
        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.quantity, Decimal("5.00"))
        self.assertEqual(batch_b.purchase_price, Decimal("150000.00"))
        self.assertEqual(batch_b.selling_price, Decimal("180000.00"))

        # Assert price resolution helpers
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_b, product),
            Decimal("180000.00"),
        )
        self.assertEqual(
            StockAllocationService.resolve_purchase_price(self.store_b, product),
            Decimal("150000.00"),
        )

    # 2. destination eski stock narxi overwrite qilinmaydi
    def test_02_destination_old_stock_price_not_overwritten(self):
        """
        Store B already has old stock: 20 @ 100 000 / 120 000.
        Store A has new stock: 10 @ 150 000 / 180 000.
        Transfer 5 units from A to B.
        Destination Store B should have:
          old lot: 20 @ 100 000 / 120 000
          transfer lot: 5 @ 150 000 / 180 000
          batch quantity = 25
          batch selling_price should NOT be overwritten (remains 120 000 until old lot exhausted)
        """
        product = self.create_product("Shock Absorber")

        # Store B has old stock
        lot_b_old, _ = self.create_stock_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("20.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("120000.00"),
        )

        # Store A has new stock
        lot_a_new, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("150000.00"),
            selling_price=Decimal("180000.00"),
        )

        transfer_item = self.create_transfer_item(
            from_store=self.store_a,
            to_store=self.store_b,
            product=product,
            quantity=Decimal("5.00"),
        )
        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        self.assertEqual(len(in_allocs), 1)
        dest_lot = in_allocs[0].lot

        # Check destination lots
        lot_b_old.refresh_from_db()
        self.assertEqual(lot_b_old.remaining_quantity, Decimal("20.00"))
        self.assertEqual(lot_b_old.purchase_price, Decimal("100000.00"))
        self.assertEqual(lot_b_old.selling_price, Decimal("120000.00"))

        self.assertEqual(dest_lot.remaining_quantity, Decimal("5.00"))
        self.assertEqual(dest_lot.purchase_price, Decimal("150000.00"))
        self.assertEqual(dest_lot.selling_price, Decimal("180000.00"))

        # Batch in Store B
        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.quantity, Decimal("25.00"))
        # Crucial: Old price must NOT be prematurely overwritten!
        self.assertEqual(batch_b.purchase_price, Decimal("100000.00"))
        self.assertEqual(batch_b.selling_price, Decimal("120000.00"))

        # Active FIFO price resolver in Store B still points to old lot
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_b, product),
            Decimal("120000.00"),
        )

    # 3. FIFO transfer
    def test_03_fifo_transfer_takes_older_lot_first(self):
        """
        Store A has:
          Lot 1: 10 @ 100 000 / 120 000
          Lot 2: 10 @ 150 000 / 180 000
        Transfer 5 units from A to B.
        FIFO must take from Lot 1.
        Dest lot gets 100 000 / 120 000.
        Source Lot 1 has 5 remaining, Lot 2 has 10 remaining.
        """
        product = self.create_product("Air Filter")

        lot_1, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("120000.00"),
        )
        lot_2, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("150000.00"),
            selling_price=Decimal("180000.00"),
        )

        transfer_item = self.create_transfer_item(
            from_store=self.store_a,
            to_store=self.store_b,
            product=product,
            quantity=Decimal("5.00"),
        )
        out_allocs = StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        self.assertEqual(len(out_allocs), 1)
        self.assertEqual(out_allocs[0].lot, lot_1)
        self.assertEqual(out_allocs[0].quantity, Decimal("5.00"))

        lot_1.refresh_from_db()
        lot_2.refresh_from_db()
        self.assertEqual(lot_1.remaining_quantity, Decimal("5.00"))
        self.assertEqual(lot_2.remaining_quantity, Decimal("10.00"))

        self.assertEqual(len(in_allocs), 1)
        dest_lot = in_allocs[0].lot
        self.assertEqual(dest_lot.source_lot, lot_1)
        self.assertEqual(dest_lot.purchase_price, Decimal("100000.00"))
        self.assertEqual(dest_lot.selling_price, Decimal("120000.00"))

    # 4. mixed-price transfer
    def test_04_mixed_price_transfer_creates_distinct_destination_lots(self):
        """
        Store A has:
          Lot 1: 3 @ 100 000 / 120 000
          Lot 2: 5 @ 150 000 / 180 000
        Transfer 6 units.
        FIFO allocates:
          3 units from Lot 1 @ 100 000
          3 units from Lot 2 @ 150 000
        Destination must receive TWO separate lots:
          Dest Lot 1: 3 @ 100 000 / 120 000 (source: Lot 1)
          Dest Lot 2: 3 @ 150 000 / 180 000 (source: Lot 2)
        Total transfer quantity = 6.
        """
        product = self.create_product("Fuel Pump")

        lot_1, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("3.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("120000.00"),
        )
        lot_2, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("150000.00"),
            selling_price=Decimal("180000.00"),
        )

        transfer_item = self.create_transfer_item(
            from_store=self.store_a,
            to_store=self.store_b,
            product=product,
            quantity=Decimal("6.00"),
        )
        out_allocs = StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        self.assertEqual(len(out_allocs), 2)
        self.assertEqual(len(in_allocs), 2)

        dest_lot_1 = in_allocs[0].lot
        dest_lot_2 = in_allocs[1].lot

        self.assertEqual(dest_lot_1.source_lot, lot_1)
        self.assertEqual(dest_lot_1.remaining_quantity, Decimal("3.00"))
        self.assertEqual(dest_lot_1.purchase_price, Decimal("100000.00"))
        self.assertEqual(dest_lot_1.selling_price, Decimal("120000.00"))

        self.assertEqual(dest_lot_2.source_lot, lot_2)
        self.assertEqual(dest_lot_2.remaining_quantity, Decimal("3.00"))
        self.assertEqual(dest_lot_2.purchase_price, Decimal("150000.00"))
        self.assertEqual(dest_lot_2.selling_price, Decimal("180000.00"))

        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.quantity, Decimal("6.00"))

    # 5. source_lot lineage & multi-hop transfer
    def test_05_source_lot_lineage_and_multi_hop_transfer(self):
        """
        Transfer chain: Store A -> Store B -> Store C.
        Verifies source_lot pointer chain and preservation of supplier and prices.
        """
        product = self.create_product("Timing Belt")

        lot_a, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("250000.00"),
            selling_price=Decimal("320000.00"),
        )

        # Hop 1: A -> B
        t1_item = self.create_transfer_item(self.store_a, self.store_b, product, Decimal("8.00"))
        StockAllocationService.allocate_transfer_out(transfer_item=t1_item)
        in_b = StockAllocationService.allocate_transfer_in(transfer_item=t1_item)
        lot_b = in_b[0].lot

        self.assertEqual(lot_b.source_lot, lot_a)
        self.assertEqual(lot_b.supplier, self.supplier)
        self.assertEqual(lot_b.purchase_price, Decimal("250000.00"))
        self.assertEqual(lot_b.selling_price, Decimal("320000.00"))

        # Hop 2: B -> C
        t2_item = self.create_transfer_item(self.store_b, self.store_c, product, Decimal("4.00"))
        StockAllocationService.allocate_transfer_out(transfer_item=t2_item)
        in_c = StockAllocationService.allocate_transfer_in(transfer_item=t2_item)
        lot_c = in_c[0].lot

        self.assertEqual(lot_c.source_lot, lot_b)
        self.assertEqual(lot_c.source_lot.source_lot, lot_a)
        self.assertEqual(lot_c.supplier, self.supplier)
        self.assertEqual(lot_c.purchase_price, Decimal("250000.00"))
        self.assertEqual(lot_c.selling_price, Decimal("320000.00"))

    # 6. purchase price preservation
    def test_06_purchase_price_preservation_and_unit_cost(self):
        """Checks decimal precision and unit_cost correctness on allocations."""
        product = self.create_product("Clutch Plate")

        lot_src, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("87654.32"),
            selling_price=Decimal("123456.78"),
        )

        transfer_item = self.create_transfer_item(self.store_a, self.store_b, product, Decimal("3.00"))
        out_allocs = StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        self.assertEqual(out_allocs[0].unit_cost, Decimal("87654.32"))
        self.assertEqual(in_allocs[0].unit_cost, Decimal("87654.32"))
        self.assertEqual(in_allocs[0].lot.purchase_price, Decimal("87654.32"))

    # 7. selling price resolution
    def test_07_selling_price_resolution(self):
        """
        Tests StockLot.resolve_selling_price(), property selling_price,
        and StockAllocationService.resolve_selling_price() transitions.
        """
        product = self.create_product("Spark Plug")

        lot_1, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("4.00"),
            purchase_price=Decimal("20000.00"),
            selling_price=Decimal("30000.00"),
        )
        lot_2, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("6.00"),
            purchase_price=Decimal("25000.00"),
            selling_price=Decimal("38000.00"),
        )

        # Before transfer, store A active price is 30 000
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_a, product),
            Decimal("30000.00"),
        )

        # Transfer 4 from lot_1
        transfer_item = self.create_transfer_item(self.store_a, self.store_b, product, Decimal("4.00"))
        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        # Now store A active lot is lot_2 (lot_1 remaining is 0)
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_a, product),
            Decimal("38000.00"),
        )
        # Store B active price is 30 000
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_b, product),
            Decimal("30000.00"),
        )

    # 8. destination sale uses correct price and FIFO cost
    def test_08_destination_sale_uses_correct_price_and_fifo_cost(self):
        """
        Destination Store B has:
          old lot: 5 @ 100 000 / 120 000
          transferred lot: 5 @ 150 000 / 180 000
        Sale of 7 units in Store B at price 200 000:
          FIFO: 5 units from old lot (cost 100 000), 2 units from transferred lot (cost 150 000)
          Average cost = (5*100000 + 2*150000) / 7 = 800000 / 7 = 114285.71
          Batch remaining = 3
          ProductBatch prices update to the transferred lot (150 000 / 180 000)
        """
        product = self.create_product("Brake Disc")

        # Old lot at Store B
        lot_b_old, _ = self.create_stock_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("120000.00"),
        )

        # Store A has new lot
        lot_a, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("150000.00"),
            selling_price=Decimal("180000.00"),
        )

        # Transfer 5 from Store A to Store B
        transfer_item = self.create_transfer_item(self.store_a, self.store_b, product, Decimal("5.00"))
        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)
        dest_lot = in_allocs[0].lot

        # Now Store B has 5 (old) + 5 (transferred) = 10 units
        # Sell 7 units in Store B using SaleService
        sale_data = {
            "store": self.store_b.id,
            "items": [
                {
                    "product": product.id,
                    "quantity": 7,
                    "price": Decimal("200000.00"),
                }
            ],
            "payments": [
                {
                    "type": "cash",
                    "amount": Decimal("1400000.00"),
                }
            ],
        }

        sale = SaleService.create_sale(user=self.superuser, data=sale_data)
        sale_item = sale.items.get(product=product)

        # Expected cost = (5 * 100000 + 2 * 150000) / 7 = 800000 / 7 = 114285.71
        expected_avg_cost = (Decimal("800000.00") / Decimal("7.00")).quantize(Decimal("0.01"))
        self.assertEqual(sale_item.purchase_price, expected_avg_cost)

        # Check lot remainders
        lot_b_old.refresh_from_db()
        dest_lot.refresh_from_db()
        self.assertEqual(lot_b_old.remaining_quantity, Decimal("0.00"))
        self.assertEqual(dest_lot.remaining_quantity, Decimal("3.00"))

        # Batch in Store B now reflects the remaining transferred lot's prices
        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.quantity, Decimal("3.00"))
        self.assertEqual(batch_b.purchase_price, Decimal("150000.00"))
        self.assertEqual(batch_b.selling_price, Decimal("180000.00"))

    # 9. ProductBatch synchronization
    def test_09_product_batch_synchronization(self):
        """ProductBatch quantity must always match sum of active lot remaining quantities."""
        product = self.create_product("Headlight")

        lot_1, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("8.00"),
            purchase_price=Decimal("50000.00"),
            selling_price=Decimal("70000.00"),
        )

        batch_a = ProductBatch.objects.get(store=self.store_a, product=product)
        self.assertEqual(batch_a.quantity, Decimal("8.00"))

        transfer_item = self.create_transfer_item(self.store_a, self.store_b, product, Decimal("3.00"))
        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)

        batch_a.refresh_from_db()
        self.assertEqual(batch_a.quantity, Decimal("5.00"))

        StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)
        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.quantity, Decimal("3.00"))

    # 10. StockAllocation ledger correctness
    def test_10_stock_allocation_ledger_correctness(self):
        """Verifies immutable ledger entries for TRANSFER_OUT and TRANSFER_IN."""
        product = self.create_product("Radiator")

        lot_src, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("500000.00"),
            selling_price=Decimal("650000.00"),
        )

        transfer_item = self.create_transfer_item(self.store_a, self.store_b, product, Decimal("4.00"))
        out_allocs = StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        self.assertEqual(len(out_allocs), 1)
        self.assertEqual(len(in_allocs), 1)

        out_alloc = out_allocs[0]
        in_alloc = in_allocs[0]

        self.assertEqual(out_alloc.movement_type, StockAllocation.MovementType.TRANSFER_OUT)
        self.assertEqual(out_alloc.direction, StockAllocation.Direction.OUT)
        self.assertEqual(out_alloc.quantity, Decimal("4.00"))
        self.assertEqual(out_alloc.unit_cost, Decimal("500000.00"))
        self.assertEqual(out_alloc.transfer_item, transfer_item)
        self.assertEqual(out_alloc.lot, lot_src)

        self.assertEqual(in_alloc.movement_type, StockAllocation.MovementType.TRANSFER_IN)
        self.assertEqual(in_alloc.direction, StockAllocation.Direction.IN)
        self.assertEqual(in_alloc.quantity, Decimal("4.00"))
        self.assertEqual(in_alloc.unit_cost, Decimal("500000.00"))
        self.assertEqual(in_alloc.transfer_item, transfer_item)
        self.assertEqual(in_alloc.lot.source_lot, lot_src)

        # Net stock change across both stores for this transfer item is 0
        total_delta = in_alloc.quantity - out_alloc.quantity
        self.assertEqual(total_delta, Decimal("0.00"))

    # 11. rollback on failed transfer
    def test_11_rollback_on_failed_transfer(self):
        """Insufficient stock must raise InsufficientStockError and leave all states untouched."""
        product = self.create_product("Wiper Blade")

        lot, _ = self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("2.00"),
            purchase_price=Decimal("15000.00"),
            selling_price=Decimal("25000.00"),
        )

        transfer_item = self.create_transfer_item(self.store_a, self.store_b, product, Decimal("5.00"))

        with self.assertRaises(InsufficientStockError):
            with transaction.atomic():
                StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)

        lot.refresh_from_db()
        self.assertEqual(lot.remaining_quantity, Decimal("2.00"))
        batch_a = ProductBatch.objects.get(store=self.store_a, product=product)
        self.assertEqual(batch_a.quantity, Decimal("2.00"))
        self.assertFalse(StockAllocation.objects.filter(transfer_item=transfer_item).exists())

    # 12. existing transfer regression
    def test_12_existing_transfer_service_regression(self):
        """Full high-level TransferService workflow with create_transfer and approve_transfer."""
        product_1 = self.create_product("Oil Filter")
        product_2 = self.create_product("Cabin Filter")

        self.create_stock_entry_lot(
            store=self.store_a,
            product=product_1,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("30000.00"),
            selling_price=Decimal("45000.00"),
        )
        self.create_stock_entry_lot(
            store=self.store_a,
            product=product_2,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("40000.00"),
            selling_price=Decimal("60000.00"),
        )

        transfer = TransferService.create_transfer(
            from_store=self.store_a,
            to_store=self.store_b,
            items_data=[
                {"product": product_1, "quantity": 4},
                {"product": product_2, "quantity": 6},
            ],
            user=self.superuser,
        )
        self.assertEqual(transfer.status, StockTransfer.Status.PENDING)

        approved = TransferService.approve_transfer(user=self.superuser, transfer_id=transfer.id)
        self.assertEqual(approved.status, StockTransfer.Status.APPROVED)

        # Verify Store B received correct batches and prices
        batch_1 = ProductBatch.objects.get(store=self.store_b, product=product_1)
        batch_2 = ProductBatch.objects.get(store=self.store_b, product=product_2)

        self.assertEqual(batch_1.quantity, Decimal("4.00"))
        self.assertEqual(batch_1.purchase_price, Decimal("30000.00"))
        self.assertEqual(batch_1.selling_price, Decimal("45000.00"))

        self.assertEqual(batch_2.quantity, Decimal("6.00"))
        self.assertEqual(batch_2.purchase_price, Decimal("40000.00"))
        self.assertEqual(batch_2.selling_price, Decimal("60000.00"))

    # 13. Supplier Sales regression
    def test_13_supplier_sales_report_regression(self):
        """
        After transfer and subsequent sale in destination store,
        SupplierSalesReportService must attribute sales correctly to original supplier.
        """
        product = self.create_product("Alternator")

        # Initial entry at Store A from supplier
        self.create_stock_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("150000.00"),
            supplier=self.supplier,
        )

        # Transfer to Store B
        transfer_item = self.create_transfer_item(self.store_a, self.store_b, product, Decimal("3.00"))
        StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)

        # Sell 2 units in Store B
        sale_data = {
            "store": self.store_b.id,
            "items": [
                {
                    "product": product.id,
                    "quantity": 2,
                    "price": Decimal("160000.00"),
                }
            ],
            "payments": [
                {
                    "type": "cash",
                    "amount": Decimal("320000.00"),
                }
            ],
        }
        sale = SaleService.create_sale(user=self.superuser, data=sale_data)

        # Query supplier sales report
        today = timezone.localdate()
        cols, rows, _, summary = SupplierSalesReportService.build_report(
            params={
                "from": str(today - timedelta(days=1)),
                "to": str(today + timedelta(days=1)),
                "supplier": str(self.supplier.id),
                "store": str(self.store_b.id),
            },
            user=self.superuser,
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["supplier"], self.supplier.name)
        self.assertEqual(row["sold_qty"], Decimal("2.00"))
        self.assertEqual(Decimal(str(row["revenue"])), Decimal("320000.00"))

        summary_dict = {s["label"]: s["value"] for s in summary}
        self.assertEqual(summary_dict["Jami sotilgan"], Decimal("2.00"))
        self.assertEqual(summary_dict["Jami tushum"], "320000.00")
