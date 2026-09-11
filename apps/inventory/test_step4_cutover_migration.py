from datetime import datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.inventory.models import StockLot
from apps.inventory.services.stock_reconciliation_service import (
    StockReconciliationReport,
    StockReconciliationService,
)
from apps.products.models import Product, ProductBatch
from apps.store.models import Store


class Step4CutoffMigrationTests(TestCase):
    """
    Focused tests for Phase 1.8 Step 4: Legacy Stock -> StockLot Cut-off Migration.
    Validates the 16 core requirements:
    1. positive ProductBatch creates opening lot
    2. zero quantity creates no lot
    3. negative quantity creates no lot
    4. supplier is NULL
    5. correct purchase_price copied
    6. correct initial_quantity
    7. correct remaining_quantity
    8. correct lot_type
    9. correct cutoff timestamp
    10. idempotent second execution
    11. dry-run does not modify DB
    12. invariant after migration
    13. multiple stores/products
    14. duplicate opening lot protection
    15. reconciliation detects mismatch
    16. reconciliation does not modify data
    """

    def setUp(self):
        super().setUp()
        self.store1 = Store.objects.create(
            name="Alpha Warehouse",
            address="Tashkent 1",
            phone_number="+998901111111",
            type=Store.StoreType.STORE,
        )
        self.store2 = Store.objects.create(
            name="Beta Warehouse",
            address="Tashkent 2",
            phone_number="+998902222222",
            type=Store.StoreType.STORE,
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
        self.product3 = Product.objects.create(
            name="Spark Plug NGK",
            barcode="4781001000032",
            status=Product.ProductStatus.ACTIVE,
        )

    # 1. positive ProductBatch creates opening lot
    def test_01_positive_batch_creates_opening_lot(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("15.00"),
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
        )
        out = StringIO()
        call_command("migrate_legacy_stock_to_lots", stdout=out)

        lots = StockLot.objects.filter(store=self.store1, product=self.product1)
        self.assertEqual(lots.count(), 1)
        lot = lots.first()
        self.assertEqual(lot.lot_type, StockLot.LotType.OPENING_BALANCE)
        self.assertEqual(lot.remaining_quantity, Decimal("15.00"))

    # 2. zero quantity creates no lot
    def test_02_zero_quantity_creates_no_lot(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("0.00"),
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
        )
        call_command("migrate_legacy_stock_to_lots")
        self.assertFalse(
            StockLot.objects.filter(store=self.store1, product=self.product1).exists()
        )

    # 3. negative quantity creates no lot
    def test_03_negative_quantity_creates_no_lot(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("-5.00"),
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
        )
        call_command("migrate_legacy_stock_to_lots")
        self.assertFalse(
            StockLot.objects.filter(store=self.store1, product=self.product1).exists()
        )

    # 4. supplier is NULL
    def test_04_supplier_is_null(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("20.00"),
            purchase_price=Decimal("80.00"),
            selling_price=Decimal("120.00"),
        )
        call_command("migrate_legacy_stock_to_lots")
        lot = StockLot.objects.get(store=self.store1, product=self.product1)
        self.assertIsNone(lot.supplier)

    # 5. correct purchase_price copied
    def test_05_correct_purchase_price_copied(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("123.45"),
            selling_price=Decimal("160.00"),
        )
        call_command("migrate_legacy_stock_to_lots")
        lot = StockLot.objects.get(store=self.store1, product=self.product1)
        self.assertEqual(lot.purchase_price, Decimal("123.45"))

    # 6. correct initial_quantity
    def test_06_correct_initial_quantity(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("42.50"),
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("80.00"),
        )
        call_command("migrate_legacy_stock_to_lots")
        lot = StockLot.objects.get(store=self.store1, product=self.product1)
        self.assertEqual(lot.initial_quantity, Decimal("42.50"))

    # 7. correct remaining_quantity
    def test_07_correct_remaining_quantity(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("42.50"),
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("80.00"),
        )
        call_command("migrate_legacy_stock_to_lots")
        lot = StockLot.objects.get(store=self.store1, product=self.product1)
        self.assertEqual(lot.remaining_quantity, Decimal("42.50"))

    # 8. correct lot_type
    def test_08_correct_lot_type(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("80.00"),
        )
        call_command("migrate_legacy_stock_to_lots")
        lot = StockLot.objects.get(store=self.store1, product=self.product1)
        self.assertEqual(lot.lot_type, StockLot.LotType.OPENING_BALANCE)

    # 9. correct cutoff timestamp
    def test_09_correct_cutoff_timestamp(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("80.00"),
        )
        cutoff_input = "2026-08-15 14:30:00"
        call_command("migrate_legacy_stock_to_lots", cutoff=cutoff_input)
        lot = StockLot.objects.get(store=self.store1, product=self.product1)

        expected_dt = timezone.make_aware(
            datetime(2026, 8, 15, 14, 30, 0),
            timezone.get_current_timezone(),
        )
        self.assertEqual(lot.created_at, expected_dt)

    # 10. idempotent second execution
    def test_10_idempotent_second_execution(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("80.00"),
        )
        out1 = StringIO()
        call_command("migrate_legacy_stock_to_lots", stdout=out1)
        self.assertEqual(StockLot.objects.filter(store=self.store1, product=self.product1).count(), 1)

        out2 = StringIO()
        call_command("migrate_legacy_stock_to_lots", stdout=out2)
        # Count must remain exactly 1 — no duplicate lot
        self.assertEqual(StockLot.objects.filter(store=self.store1, product=self.product1).count(), 1)
        self.assertIn("Skipped (already migrated)       : 1", out2.getvalue())

    # 11. dry-run does not modify DB
    def test_11_dry_run_does_not_modify_db(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("80.00"),
        )
        out = StringIO()
        call_command("migrate_legacy_stock_to_lots", dry_run=True, stdout=out)
        self.assertEqual(StockLot.objects.count(), 0)
        self.assertIn("DRY RUN COMPLETE", out.getvalue())
        self.assertIn("Lots that would be created       : 1", out.getvalue())

    # 12. invariant after migration
    def test_12_invariant_after_migration(self):
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("80.00"),
        )
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product2,
            quantity=Decimal("25.00"),
            purchase_price=Decimal("70.00"),
            selling_price=Decimal("110.00"),
        )
        call_command("migrate_legacy_stock_to_lots")

        report = StockReconciliationService.reconcile_all(store_id=self.store1.id)
        self.assertTrue(report.is_valid)
        self.assertEqual(len(report.issues), 0)

    # 13. multiple stores and products
    def test_13_multiple_stores_and_products(self):
        # Store 1
        ProductBatch.objects.create(store=self.store1, product=self.product1, quantity=Decimal("10.00"), purchase_price=Decimal("100.00"), selling_price=Decimal("150.00"))
        ProductBatch.objects.create(store=self.store1, product=self.product2, quantity=Decimal("20.00"), purchase_price=Decimal("200.00"), selling_price=Decimal("250.00"))
        ProductBatch.objects.create(store=self.store1, product=self.product3, quantity=Decimal("0.00"), purchase_price=Decimal("300.00"), selling_price=Decimal("350.00"))

        # Store 2
        ProductBatch.objects.create(store=self.store2, product=self.product1, quantity=Decimal("5.00"), purchase_price=Decimal("100.00"), selling_price=Decimal("150.00"))
        ProductBatch.objects.create(store=self.store2, product=self.product2, quantity=Decimal("-2.00"), purchase_price=Decimal("200.00"), selling_price=Decimal("250.00"))
        ProductBatch.objects.create(store=self.store2, product=self.product3, quantity=Decimal("15.00"), purchase_price=Decimal("300.00"), selling_price=Decimal("350.00"))

        call_command("migrate_legacy_stock_to_lots")

        # Eligible batches: Store1(p1, p2), Store2(p1, p3) => exactly 4 lots
        self.assertEqual(StockLot.objects.count(), 4)

        lot_s1_p1 = StockLot.objects.get(store=self.store1, product=self.product1)
        self.assertEqual(lot_s1_p1.remaining_quantity, Decimal("10.00"))

        lot_s1_p2 = StockLot.objects.get(store=self.store1, product=self.product2)
        self.assertEqual(lot_s1_p2.remaining_quantity, Decimal("20.00"))

        lot_s2_p1 = StockLot.objects.get(store=self.store2, product=self.product1)
        self.assertEqual(lot_s2_p1.remaining_quantity, Decimal("5.00"))

        lot_s2_p3 = StockLot.objects.get(store=self.store2, product=self.product3)
        self.assertEqual(lot_s2_p3.remaining_quantity, Decimal("15.00"))

        # Invariant check
        report = StockReconciliationService.reconcile_all()
        self.assertTrue(report.is_valid)

    # 14. duplicate opening lot protection
    def test_14_duplicate_opening_lot_protection(self):
        batch = ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("80.00"),
        )
        # Pre-create an opening lot
        StockLot.objects.create(
            store=self.store1,
            product=self.product1,
            lot_type=StockLot.LotType.OPENING_BALANCE,
            initial_quantity=Decimal("10.00"),
            remaining_quantity=Decimal("10.00"),
            purchase_price=Decimal("50.00"),
        )
        # Run migration
        call_command("migrate_legacy_stock_to_lots")

        # Opening lots for this product must remain 1
        opening_lots = StockLot.objects.filter(
            store=self.store1,
            product=self.product1,
            lot_type=StockLot.LotType.OPENING_BALANCE,
        )
        self.assertEqual(opening_lots.count(), 1)

    # 15. reconciliation detects mismatch
    def test_15_reconciliation_detects_mismatch(self):
        # Create batch with qty=10
        ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("80.00"),
        )
        # Create lot with qty=7 (mismatch of 3)
        StockLot.objects.create(
            store=self.store1,
            product=self.product1,
            lot_type=StockLot.LotType.OPENING_BALANCE,
            initial_quantity=Decimal("7.00"),
            remaining_quantity=Decimal("7.00"),
            purchase_price=Decimal("50.00"),
        )
        # Create orphan lot (product3 in store2 with no ProductBatch)
        orphan = StockLot.objects.create(
            store=self.store2,
            product=self.product3,
            lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=Decimal("5.00"),
            remaining_quantity=Decimal("5.00"),
            purchase_price=Decimal("50.00"),
        )

        report = StockReconciliationService.reconcile_all()
        self.assertFalse(report.is_valid)
        self.assertEqual(len(report.quantity_mismatches), 1)
        self.assertEqual(report.quantity_mismatches[0]["difference"], Decimal("3.00"))
        self.assertEqual(len(report.orphan_lots), 1)
        self.assertEqual(report.orphan_lots[0]["lot_id"], orphan.id)

    # 16. reconciliation does not modify data
    def test_16_reconciliation_does_not_modify_data(self):
        batch = ProductBatch.objects.create(
            store=self.store1,
            product=self.product1,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("80.00"),
        )
        lot = StockLot.objects.create(
            store=self.store1,
            product=self.product1,
            lot_type=StockLot.LotType.OPENING_BALANCE,
            initial_quantity=Decimal("10.00"),
            remaining_quantity=Decimal("10.00"),
            purchase_price=Decimal("50.00"),
        )
        init_lot_count = StockLot.objects.count()
        init_batch_count = ProductBatch.objects.count()

        # Run both reconciliation service and command
        report = StockReconciliationService.reconcile_all()
        out = StringIO()
        call_command("reconcile_stock_lots", stdout=out)

        # Assert no counts or values changed
        self.assertEqual(StockLot.objects.count(), init_lot_count)
        self.assertEqual(ProductBatch.objects.count(), init_batch_count)

        batch.refresh_from_db()
        lot.refresh_from_db()
        self.assertEqual(batch.quantity, Decimal("10.00"))
        self.assertEqual(lot.remaining_quantity, Decimal("10.00"))
