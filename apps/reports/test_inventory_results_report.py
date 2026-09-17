import csv
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
import io
import openpyxl

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.inventory.models import (
    InventoryCount,
    InventoryMovement,
    InventorySession,
    InventorySnapshot,
    StockAllocation,
    StockLot,
)
from apps.inventory.services.inventory_service import InventoryService
from apps.products.models import Brand, Category, Product, ProductBatch, ProductUnitMeasurement
from apps.reports.services.reporting_foundation import ReportingFoundationService
from apps.reports.services.report_builder import ReportBuilderService
from apps.reports.views.report_builder_view import (
    ReportBuilderExportAPIView,
    ReportBuilderGenerateAPIView,
)
from apps.sales.models import Sale, SaleItem
from apps.store.models import Store, StoreUser
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.users.models.role import Role
from apps.users.models.user import User
from apps.writeoff.models import WriteOff, WriteOffItem


class InventoryResultsExpandedReportTests(TestCase):
    """
    AutoCRM Inventarizatsiya natijalari hisoboti — Billz darajasiga kengaytirilgan
    to'liq integratsion va unit testlar to'plami (Phase: Inventory Results Parity).
    """

    @classmethod
    def setUpTestData(cls):
        cls.factory = APIRequestFactory()

        # 1. Do'konlar
        cls.store1 = Store.objects.create(name="Filial 1", address="Toshkent 1", phone_number="+998901111111")
        cls.store2 = Store.objects.create(name="Filial 2", address="Toshkent 2", phone_number="+998902222222")

        # 2. Rollar va foydalanuvchilar
        cls.role_admin = Role.objects.create(name="Super Admin", permissions=["*"])
        cls.role_store = Role.objects.create(
            name="Store User",
            permissions=[
                "reports.view",
                "reports.inventory_results.view",
                "reports.inventory_results.export",
            ],
        )

        cls.admin = User.objects.create_superuser(
            phone_number="+998990001111",
            full_name="Admin User",
            role=cls.role_admin,
        )
        cls.store_user = User.objects.create_user(
            phone_number="+998990002222",
            full_name="Filial Xodimi",
            role=cls.role_store,
        )
        StoreUser.objects.create(user=cls.store_user, store=cls.store1, is_active=True)

        # 3. Kategoriya va Brend
        cls.cat1 = Category.objects.create(name="Filtrlar", slug="filtrlar")
        cls.brand1 = Brand.objects.create(name="Mann Filter")
        cls.unit_dona = ProductUnitMeasurement.objects.create(measurement="dona")

    def _create_session(self, store, started_at=None, status=InventorySession.Status.COMPLETED):
        sess = InventorySession.objects.create(
            store=store,
            started_by=self.admin,
            status=status,
            snapshot_taken=True,
        )
        if started_at:
            InventorySession.objects.filter(id=sess.id).update(started_at=started_at)
            sess.refresh_from_db()
        return sess

    def _create_alloc(self, **kwargs):
        c_at = kwargs.pop("created_at", None)
        alloc = StockAllocation.objects.create(**kwargs)
        if c_at:
            StockAllocation.objects.filter(id=alloc.id).update(created_at=c_at)
            alloc.refresh_from_db()
        return alloc

    # ──────────────────────────────────────────────────────────────────────────
    # 1. expected_qty snapshotdan olinadi
    # ──────────────────────────────────────────────────────────────────────────
    def test_01_expected_qty_taken_strictly_from_snapshot(self):
        sess = self._create_session(self.store1, started_at=datetime(2026, 9, 1, 10, 0, 0, tzinfo=dt_timezone.utc))
        p = Product.objects.create(name="Test Prod 1", sku="SKU-01")
        # ProductBatch qoldig'i 25 bo'lsa ham snapshot 100
        ProductBatch.objects.create(store=self.store1, product=p, quantity=Decimal("25"), purchase_price=Decimal("10000"), selling_price=Decimal("15000"))
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("100"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("100"), is_check=True)

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["expected_qty"], 100.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 2. counted_qty faqat checked countlardan olinadi
    # ──────────────────────────────────────────────────────────────────────────
    def test_02_counted_qty_from_verified_counts_only(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Test Prod 2", sku="SKU-02")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("50"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("45"), is_check=True)

        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["counted_qty"], 45.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 3. unchecked item shortage qilinmaydi
    # ──────────────────────────────────────────────────────────────────────────
    def test_03_unchecked_item_not_treated_as_shortage(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Test Unchecked", sku="SKU-03")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("80"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("0"), is_check=False)

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        row = rows[0]
        self.assertEqual(row["status"], "unchecked")
        self.assertIsNone(row["counted_qty"])
        self.assertIsNone(row["difference_qty"])
        self.assertEqual(row["shortage_qty"], 0.0)
        self.assertEqual(row["final_balance"], 80.0)
        self.assertEqual(totals["unchecked_count"], 1)

    # ──────────────────────────────────────────────────────────────────────────
    # 4. difference formula: counted - expected
    # ──────────────────────────────────────────────────────────────────────────
    def test_04_difference_formula(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Test Diff", sku="SKU-04")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("50"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("42"), is_check=True)

        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["difference_qty"], -8.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 5. shortage formula: max(expected - counted, 0)
    # ──────────────────────────────────────────────────────────────────────────
    def test_05_shortage_formula(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Test Shortage", sku="SKU-05")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("30"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("20"), is_check=True)

        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["shortage_qty"], 10.0)
        self.assertEqual(rows[0]["excess_qty"], 0.0)
        self.assertEqual(rows[0]["status"], "shortage")

    # ──────────────────────────────────────────────────────────────────────────
    # 6. excess formula: max(counted - expected, 0)
    # ──────────────────────────────────────────────────────────────────────────
    def test_06_excess_formula(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Test Excess", sku="SKU-06")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("20"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("27"), is_check=True)

        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["excess_qty"], 7.0)
        self.assertEqual(rows[0]["shortage_qty"], 0.0)
        self.assertEqual(rows[0]["status"], "excess")

    # ──────────────────────────────────────────────────────────────────────────
    # 7. sale during inventory: StockAllocation & InventoryMovement
    # ──────────────────────────────────────────────────────────────────────────
    def test_07_sale_during_inventory(self):
        start_time = datetime(2026, 9, 2, 10, 0, 0, tzinfo=dt_timezone.utc)
        sess = self._create_session(self.store1, started_at=start_time)
        p = Product.objects.create(name="Test Sale Prod", sku="SKU-07")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("40"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("35"), is_check=True, counted_at=start_time)

        # StockAllocation orqali sotuv
        lot = StockLot.objects.create(
            store=self.store1, product=p, lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=Decimal("50"), remaining_quantity=Decimal("45"), purchase_price=Decimal("10000"),
        )
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin, total_amount=Decimal("50000"), paid_amount=Decimal("50000"), status=Sale.Status.PAID,
        )
        sale_item = SaleItem.objects.create(
            sale=sale, product=p, quantity=Decimal("5"), unit_price=Decimal("15000"), total_price=Decimal("75000"),
        )
        self._create_alloc(
            lot=lot, movement_type=StockAllocation.MovementType.SALE, direction=StockAllocation.Direction.OUT,
            quantity=Decimal("5"), unit_cost=Decimal("10000"),
            sale_item=sale_item,
            created_at=datetime(2026, 9, 2, 11, 0, 0, tzinfo=dt_timezone.utc),
        )

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["period_sold_qty"], 5.0)
        self.assertEqual(totals["total_period_sold_qty"], 5.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 8. write-off during inventory
    # ──────────────────────────────────────────────────────────────────────────
    def test_08_write_off_during_inventory(self):
        start_time = datetime(2026, 9, 2, 10, 0, 0, tzinfo=dt_timezone.utc)
        sess = self._create_session(self.store1, started_at=start_time)
        p = Product.objects.create(name="Test Spisaniye Prod", sku="SKU-08")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("50"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("50"), is_check=True, counted_at=start_time)

        lot = StockLot.objects.create(
            store=self.store1, product=p, lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=Decimal("50"), remaining_quantity=Decimal("48"), purchase_price=Decimal("10000"),
        )
        wo = WriteOff.objects.create(store=self.store1, created_by=self.admin, reason=WriteOff.Reason.DAMAGED)
        woi = WriteOffItem.objects.create(write_off=wo, product=p, quantity=Decimal("2"), purchase_price=Decimal("10000"), selling_price=Decimal("15000"))
        self._create_alloc(
            lot=lot, movement_type=StockAllocation.MovementType.WRITE_OFF, direction=StockAllocation.Direction.OUT,
            quantity=Decimal("2"), unit_cost=Decimal("10000"),
            write_off_item=woi,
            created_at=datetime(2026, 9, 2, 11, 0, 0, tzinfo=dt_timezone.utc),
        )

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["period_write_off_qty"], 2.0)
        self.assertEqual(totals["total_period_write_off_qty"], 2.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 9. transfer out during inventory
    # ──────────────────────────────────────────────────────────────────────────
    def test_09_transfer_out_during_inventory(self):
        start_time = datetime(2026, 9, 2, 10, 0, 0, tzinfo=dt_timezone.utc)
        sess = self._create_session(self.store1, started_at=start_time)
        p = Product.objects.create(name="Test Transfer Out", sku="SKU-09")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("50"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("50"), is_check=True, counted_at=start_time)

        lot = StockLot.objects.create(
            store=self.store1, product=p, lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=Decimal("50"), remaining_quantity=Decimal("47"), purchase_price=Decimal("10000"),
        )
        st = StockTransfer.objects.create(from_store=self.store1, to_store=self.store2, status=StockTransfer.Status.APPROVED, created_by=self.admin)
        ti = StockTransferItem.objects.create(stock_transfer=st, product=p, quantity=Decimal("3"), purchase_price=Decimal("10000"), selling_price=Decimal("15000"))
        self._create_alloc(
            lot=lot, movement_type=StockAllocation.MovementType.TRANSFER_OUT, direction=StockAllocation.Direction.OUT,
            quantity=Decimal("3"), unit_cost=Decimal("10000"),
            transfer_item=ti,
            created_at=datetime(2026, 9, 2, 11, 30, 0, tzinfo=dt_timezone.utc),
        )

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["period_transfer_out_qty"], 3.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 10. transfer in during inventory
    # ──────────────────────────────────────────────────────────────────────────
    def test_10_transfer_in_during_inventory(self):
        start_time = datetime(2026, 9, 2, 10, 0, 0, tzinfo=dt_timezone.utc)
        sess = self._create_session(self.store1, started_at=start_time)
        p = Product.objects.create(name="Test Transfer In", sku="SKU-10")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("50"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("50"), is_check=True, counted_at=start_time)

        source_lot = StockLot.objects.create(
            store=self.store2, product=p, lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=Decimal("10"), remaining_quantity=Decimal("0"), purchase_price=Decimal("10000"),
        )
        lot = StockLot.objects.create(
            store=self.store1, product=p, lot_type=StockLot.LotType.TRANSFER_IN,
            source_lot=source_lot,
            initial_quantity=Decimal("10"), remaining_quantity=Decimal("10"), purchase_price=Decimal("10000"),
        )
        st = StockTransfer.objects.create(from_store=self.store2, to_store=self.store1, status=StockTransfer.Status.APPROVED, created_by=self.admin)
        ti = StockTransferItem.objects.create(stock_transfer=st, product=p, quantity=Decimal("10"), purchase_price=Decimal("10000"), selling_price=Decimal("15000"))
        self._create_alloc(
            lot=lot, movement_type=StockAllocation.MovementType.TRANSFER_IN, direction=StockAllocation.Direction.IN,
            quantity=Decimal("10"), unit_cost=Decimal("10000"),
            transfer_item=ti,
            created_at=datetime(2026, 9, 2, 11, 30, 0, tzinfo=dt_timezone.utc),
        )

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["period_transfer_in_qty"], 10.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 11. inventory shortage allocation
    # ──────────────────────────────────────────────────────────────────────────
    def test_11_inventory_shortage_allocation(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Shortage Alloc Prod", sku="SKU-11")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("20"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("16"), is_check=True)

        lot = StockLot.objects.create(
            store=self.store1, product=p, lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=Decimal("20"), remaining_quantity=Decimal("16"), purchase_price=Decimal("12000"),
        )
        StockAllocation.objects.create(
            lot=lot, inventory_session=sess,
            movement_type=StockAllocation.MovementType.INVENTORY_SHORTAGE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("4"), unit_cost=Decimal("12000"),
        )

        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["auto_shortage_qty"], 4.0)
        self.assertEqual(rows[0]["unit_purchase_cost"], 12000.0)
        self.assertEqual(rows[0]["shortage_purchase_value"], 48000.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 12. inventory excess allocation
    # ──────────────────────────────────────────────────────────────────────────
    def test_12_inventory_excess_allocation(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Excess Alloc Prod", sku="SKU-12")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("20"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("25"), is_check=True)

        lot = StockLot.objects.create(
            store=self.store1, product=p, lot_type=StockLot.LotType.INVENTORY_EXCESS,
            initial_quantity=Decimal("5"), remaining_quantity=Decimal("5"), purchase_price=Decimal("15000"),
        )
        StockAllocation.objects.create(
            lot=lot, inventory_session=sess,
            movement_type=StockAllocation.MovementType.INVENTORY_EXCESS,
            direction=StockAllocation.Direction.IN,
            quantity=Decimal("5"), unit_cost=Decimal("15000"),
        )

        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["auto_excess_qty"], 5.0)
        self.assertEqual(rows[0]["unit_purchase_cost"], 15000.0)
        self.assertEqual(rows[0]["excess_purchase_value"], 75000.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 13. final balance with post-count movements
    # ──────────────────────────────────────────────────────────────────────────
    def test_13_final_balance_calculation(self):
        count_time = datetime(2026, 9, 3, 12, 0, 0, tzinfo=dt_timezone.utc)
        sess = self._create_session(self.store1, started_at=datetime(2026, 9, 3, 9, 0, 0, tzinfo=dt_timezone.utc))
        p = Product.objects.create(name="Final Balance Test", sku="SKU-13")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("100"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("100"), is_check=True, counted_at=count_time)

        # Sanoqdan keyin 10 dona sotildi
        m = InventoryMovement.objects.create(
            session=sess, product=p, quantity=Decimal("10"),
            type=InventoryMovement.Type.SALE, ref_id=1,
        )
        InventoryMovement.objects.filter(id=m.id).update(created_at=datetime(2026, 9, 3, 13, 0, 0, tzinfo=dt_timezone.utc))

        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["counted_qty"], 100.0)
        self.assertEqual(rows[0]["final_balance"], 90.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 14. purchase value calculation
    # ──────────────────────────────────────────────────────────────────────────
    def test_14_purchase_value_reconciliation(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Value Test", sku="SKU-14")
        ProductBatch.objects.create(store=self.store1, product=p, quantity=Decimal("10"), purchase_price=Decimal("5000"), selling_price=Decimal("8000"))
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("7"), is_check=True)

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["shortage_qty"], 3.0)
        self.assertEqual(rows[0]["unit_purchase_cost"], 5000.0)
        self.assertEqual(rows[0]["shortage_purchase_value"], 15000.0)
        self.assertEqual(totals["total_shortage_purchase_value"], 15000.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 15. sale value calculation
    # ──────────────────────────────────────────────────────────────────────────
    def test_15_sale_value_reconciliation(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Sale Val Test", sku="SKU-15")
        ProductBatch.objects.create(store=self.store1, product=p, quantity=Decimal("10"), purchase_price=Decimal("5000"), selling_price=Decimal("8000"))
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("6"), is_check=True)

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["shortage_qty"], 4.0)
        self.assertEqual(rows[0]["unit_sale_price"], 8000.0)
        self.assertEqual(rows[0]["shortage_sale_value"], 32000.0)
        self.assertEqual(totals["total_shortage_sale_value"], 32000.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 16. store isolation in API and Export
    # ──────────────────────────────────────────────────────────────────────────
    def test_16_store_isolation_enforced(self):
        sess_store2 = self._create_session(self.store2)
        p = Product.objects.create(name="Store 2 Prod", sku="ST2-SKU")
        InventorySnapshot.objects.create(session=sess_store2, store=self.store2, product=p, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(session=sess_store2, product=p, counted_quantity=Decimal("10"), is_check=True)

        # Store 1 xodimi Store 2 sessiyasini ko'rmoqchi
        req = self.factory.get(f"/api/v1/reports/builder/generate/?report_type=inventory_results&session_id={sess_store2.id}")
        force_authenticate(req, user=self.store_user)
        resp = ReportBuilderGenerateAPIView.as_view()(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["rows"]), 0)

    # ──────────────────────────────────────────────────────────────────────────
    # 17. filters (session_id, sku, barcode, status, product_status)
    # ──────────────────────────────────────────────────────────────────────────
    def test_17_all_filter_options(self):
        sess = self._create_session(self.store1)
        p_active = Product.objects.create(name="Faol Mahsulot", sku="FLT-ACT", barcode="11111", status=Product.ProductStatus.ACTIVE)
        p_inactive = Product.objects.create(name="Nofaol Mahsulot", sku="FLT-INA", barcode="22222", status=Product.ProductStatus.INACTIVE)

        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p_active, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(session=sess, product=p_active, counted_quantity=Decimal("10"), is_check=True)

        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p_inactive, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(session=sess, product=p_inactive, counted_quantity=Decimal("8"), is_check=True)

        # Filter by sku
        rows_sku, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id, sku="FLT-ACT")
        self.assertEqual(len(rows_sku), 1)
        self.assertEqual(rows_sku[0]["sku"], "FLT-ACT")

        # Filter by product_status
        rows_act, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id, product_status="a")
        self.assertEqual(len(rows_act), 1)
        self.assertEqual(rows_act[0]["product_status"], "Faol")

        # Filter by result status
        rows_sh, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id, status="shortage")
        self.assertEqual(len(rows_sh), 1)
        self.assertEqual(rows_sh[0]["sku"], "FLT-INA")

    # ──────────────────────────────────────────────────────────────────────────
    # 18. export dataset == UI dataset
    # ──────────────────────────────────────────────────────────────────────────
    def test_18_export_dataset_equals_ui_dataset(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Dataset Equality", sku="DS-01")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("20"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("18"), is_check=True)

        params = {"report_type": "inventory_results", "session_id": str(sess.id)}
        gen_data = ReportBuilderService.generate(params, self.admin)
        _, _, exp_rows, _, _ = ReportBuilderService.export_rows(params, self.admin)

        self.assertEqual(len(gen_data["rows"]), len(exp_rows))
        self.assertEqual(gen_data["rows"][0]["sku"], exp_rows[0]["sku"])
        self.assertEqual(gen_data["rows"][0]["difference_qty"], exp_rows[0]["difference_qty"])
        self.assertEqual(gen_data["rows"][0]["shortage_qty"], exp_rows[0]["shortage_qty"])

    # ──────────────────────────────────────────────────────────────────────────
    # 19. Excel export structure
    # ──────────────────────────────────────────────────────────────────────────
    def test_19_excel_export_31_columns_and_tables(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Excel Test", sku="XLS-01")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("15"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("15"), is_check=True)

        req = self.factory.get(f"/api/v1/reports/builder/export/?report_type=inventory_results&export_type=excel&session_id={sess.id}")
        force_authenticate(req, user=self.admin)
        resp = ReportBuilderExportAPIView.as_view()(req)
        self.assertEqual(resp.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        self.assertIn("Inventarizatsiya natijalari", wb.sheetnames)
        ws = wb["Inventarizatsiya natijalari"]
        self.assertGreater(len(ws.tables), 0)
        tbl = list(ws.tables.values())[0]
        self.assertEqual(len(tbl.tableColumns), 31)

    # ──────────────────────────────────────────────────────────────────────────
    # 20. CSV export with UTF-8 BOM
    # ──────────────────────────────────────────────────────────────────────────
    def test_20_csv_export_utf8_bom(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="CSV BOM Test", sku="CSV-01")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("10"), is_check=True)

        req = self.factory.get(f"/api/v1/reports/builder/export/?report_type=inventory_results&export_type=csv&session_id={sess.id}")
        force_authenticate(req, user=self.admin)
        resp = ReportBuilderExportAPIView.as_view()(req)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.content.startswith(b"\xef\xbb\xbf"))
        csv_text = resp.content.decode("utf-8-sig")
        reader = list(csv.reader(io.StringIO(csv_text)))
        header = reader[0]
        self.assertEqual(len(header), 31, "CSV header aynan 31 ta ustundan iborat bo'lishi shart")
        data_row = reader[1]
        self.assertEqual(len(data_row), 31, "CSV data qatori aynan 31 ta ustundan iborat bo'lishi shart")

    # ──────────────────────────────────────────────────────────────────────────
    # 21. PDF export generation
    # ──────────────────────────────────────────────────────────────────────────
    def test_21_pdf_export_generation(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="PDF Test", sku="PDF-01")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("10"), is_check=True)

        req = self.factory.get(f"/api/v1/reports/builder/export/?report_type=inventory_results&export_type=pdf&session_id={sess.id}")
        force_authenticate(req, user=self.admin)
        resp = ReportBuilderExportAPIView.as_view()(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    # ──────────────────────────────────────────────────────────────────────────
    # 22. No N+1: O(1) query complexity
    # ──────────────────────────────────────────────────────────────────────────
    def test_22_no_n_plus_one_query_count(self):
        sess = self._create_session(self.store1, started_at=datetime(2026, 9, 1, 10, 0, 0, tzinfo=dt_timezone.utc))
        p = Product.objects.create(name="Query Base", sku="Q-00")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("10"), is_check=True)

        with self.assertNumQueries(9):
            ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)

        # 10 ta qo'shimcha tovar qo'shamiz
        bulk_prods = [Product(name=f"Q Prod {i}", sku=f"QP-{i}") for i in range(10)]
        Product.objects.bulk_create(bulk_prods)
        db_prods = list(Product.objects.filter(sku__startswith="QP-"))
        InventorySnapshot.objects.bulk_create([
            InventorySnapshot(session=sess, store=self.store1, product=prod, expected_quantity=Decimal("5"))
            for prod in db_prods
        ])
        InventoryCount.objects.bulk_create([
            InventoryCount(session=sess, product=prod, counted_quantity=Decimal("5"), is_check=True)
            for prod in db_prods
        ])

        # Hatto 11 ta tovar bo'lsa ham so'rovlar soni aynan 9 ta qolishi shart
        with self.assertNumQueries(9):
            ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)

        # 1,000 ta tovar bilan tekshirish (Check G: 1000+ inventory rows O(1) no N+1)
        thousand_prods = [Product(name=f"Big Q Prod {i}", sku=f"BIG-QP-{i}") for i in range(1000)]
        Product.objects.bulk_create(thousand_prods)
        db_thousand = list(Product.objects.filter(sku__startswith="BIG-QP-"))
        InventorySnapshot.objects.bulk_create([
            InventorySnapshot(session=sess, store=self.store1, product=prod, expected_quantity=Decimal("3"))
            for prod in db_thousand
        ])
        InventoryCount.objects.bulk_create([
            InventoryCount(session=sess, product=prod, counted_quantity=Decimal("3"), is_check=True)
            for prod in db_thousand
        ])

        with self.assertNumQueries(9):
            large_rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
            self.assertEqual(len(large_rows), 1011)

    # ──────────────────────────────────────────────────────────────────────────
    # 23. empty session
    # ──────────────────────────────────────────────────────────────────────────
    def test_23_empty_session_handling(self):
        sess = self._create_session(self.store1)
        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows, [])
        self.assertEqual(totals["total_rows"], 0)
        self.assertEqual(totals["total_expected_qty"], 0.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 24. fully matched inventory
    # ──────────────────────────────────────────────────────────────────────────
    def test_24_fully_matched_inventory(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Match 100%", sku="M-100")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("50"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("50"), is_check=True)

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(rows[0]["status"], "matched")
        self.assertEqual(rows[0]["difference_qty"], 0.0)
        self.assertEqual(totals["matched_count"], 1)
        self.assertEqual(totals["shortage_count"], 0)
        self.assertEqual(totals["excess_count"], 0)

    # ──────────────────────────────────────────────────────────────────────────
    # 25. shortage inventory
    # ──────────────────────────────────────────────────────────────────────────
    def test_25_shortage_inventory_totals(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Shortage Totals", sku="SH-TOT")
        ProductBatch.objects.create(store=self.store1, product=p, quantity=Decimal("10"), purchase_price=Decimal("1000"), selling_price=Decimal("2000"))
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("20"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("15"), is_check=True)

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(totals["shortage_count"], 1)
        self.assertEqual(totals["total_shortage_qty"], 5.0)
        self.assertEqual(totals["total_shortage_purchase_value"], 5000.0)
        self.assertEqual(totals["net_difference_value"], -5000.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 26. excess inventory
    # ──────────────────────────────────────────────────────────────────────────
    def test_26_excess_inventory_totals(self):
        sess = self._create_session(self.store1)
        p = Product.objects.create(name="Excess Totals", sku="EX-TOT")
        ProductBatch.objects.create(store=self.store1, product=p, quantity=Decimal("10"), purchase_price=Decimal("2000"), selling_price=Decimal("3000"))
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("14"), is_check=True)

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(totals["excess_count"], 1)
        self.assertEqual(totals["total_excess_qty"], 4.0)
        self.assertEqual(totals["total_excess_purchase_value"], 8000.0)
        self.assertEqual(totals["net_difference_value"], 8000.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 27. mixed movements during inventory
    # ──────────────────────────────────────────────────────────────────────────
    def test_27_mixed_movements_during_inventory(self):
        start = datetime(2026, 9, 4, 8, 0, 0, tzinfo=dt_timezone.utc)
        count_time = datetime(2026, 9, 4, 12, 0, 0, tzinfo=dt_timezone.utc)
        sess = self._create_session(self.store1, started_at=start)
        p = Product.objects.create(name="Mixed Prod", sku="MIX-01")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("100"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("100"), is_check=True, counted_at=count_time)

        # Sotuv: 5, Chiqish transferi: 3, Kirish transferi: 2
        for mtype, qty in [
            (InventoryMovement.Type.SALE, Decimal("5")),
            (InventoryMovement.Type.TRANSFER_OUT, Decimal("3")),
            (InventoryMovement.Type.TRANSFER_IN, Decimal("2")),
        ]:
            m = InventoryMovement.objects.create(session=sess, product=p, quantity=qty, type=mtype, ref_id=10)
            InventoryMovement.objects.filter(id=m.id).update(created_at=datetime(2026, 9, 4, 14, 0, 0, tzinfo=dt_timezone.utc))

        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        row = rows[0]
        self.assertEqual(row["period_sold_qty"], 5.0)
        self.assertEqual(row["period_transfer_out_qty"], 3.0)
        self.assertEqual(row["period_transfer_in_qty"], 2.0)
        # 100 - 5 - 3 + 2 = 94
        self.assertEqual(row["final_balance"], 94.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 28. boundary datetime
    # ──────────────────────────────────────────────────────────────────────────
    def test_28_boundary_datetime_filtering(self):
        start = datetime(2026, 9, 5, 10, 0, 0, tzinfo=dt_timezone.utc)
        sess = self._create_session(self.store1, started_at=start)
        p = Product.objects.create(name="Boundary Prod", sku="BND-01")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("50"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("50"), is_check=True, counted_at=start)

        # Sanoqdan oldingi harakat (start vaqtidan oldin)
        m_before = InventoryMovement.objects.create(session=sess, product=p, quantity=Decimal("10"), type=InventoryMovement.Type.SALE, ref_id=11)
        InventoryMovement.objects.filter(id=m_before.id).update(created_at=datetime(2026, 9, 5, 9, 0, 0, tzinfo=dt_timezone.utc))

        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        # Sanoq paytida yoki undan oldin sodir bo'lgan harakat final_balance ga ta'sir qilmasligi kerak
        self.assertEqual(rows[0]["final_balance"], 50.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 29. multiple stores separation
    # ──────────────────────────────────────────────────────────────────────────
    def test_29_multiple_stores_separation(self):
        sess1 = self._create_session(self.store1)
        sess2 = self._create_session(self.store2)

        p1 = Product.objects.create(name="P Store 1", sku="ST1-01")
        p2 = Product.objects.create(name="P Store 2", sku="ST2-01")

        InventorySnapshot.objects.create(session=sess1, store=self.store1, product=p1, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(session=sess1, product=p1, counted_quantity=Decimal("10"), is_check=True)

        InventorySnapshot.objects.create(session=sess2, store=self.store2, product=p2, expected_quantity=Decimal("20"))
        InventoryCount.objects.create(session=sess2, product=p2, counted_quantity=Decimal("20"), is_check=True)

        rows1, _ = ReportingFoundationService.get_inventory_results_metrics(store_id=self.store1.id)
        self.assertEqual(len(rows1), 1)
        self.assertEqual(rows1[0]["sku"], "ST1-01")

        rows2, _ = ReportingFoundationService.get_inventory_results_metrics(store_id=self.store2.id)
        self.assertEqual(len(rows2), 1)
        self.assertEqual(rows2[0]["sku"], "ST2-01")

    # ──────────────────────────────────────────────────────────────────────────
    # 30. multiple products bulk verification
    # ──────────────────────────────────────────────────────────────────────────
    def test_30_multiple_products_bulk(self):
        sess = self._create_session(self.store1)
        prods = [Product.objects.create(name=f"Bulk Prod {i}", sku=f"BLK-{i}") for i in range(5)]
        for i, prod in enumerate(prods):
            InventorySnapshot.objects.create(session=sess, store=self.store1, product=prod, expected_quantity=Decimal(str((i + 1) * 10)))
            InventoryCount.objects.create(session=sess, product=prod, counted_quantity=Decimal(str((i + 1) * 10)), is_check=True)

        rows, totals = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(len(rows), 5)
        self.assertEqual(totals["total_rows"], 5)
        self.assertEqual(totals["matched_count"], 5)
        # 10 + 20 + 30 + 40 + 50 = 150
        self.assertEqual(totals["total_expected_qty"], 150.0)
        self.assertEqual(totals["total_counted_qty"], 150.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 31. MANDATORY INTEGRATION SCENARIO (Section 17):
    #     Inventory boshlandi: Product A = 10
    #     Inventory davomida: SALE = 2, TRANSFER_OUT = 1, WRITE_OFF = 1
    #     Count: 6
    #     Report: Expected = 10, Counted = 6, Difference = -4, Shortage = 4
    #     va inventory interval ichidagi haqiqiy movementlar alohida ko'rinishi kerak.
    #     Bunda movementlarni shortage bilan double-count qilmaslik shart!
    # ──────────────────────────────────────────────────────────────────────────
    def test_31_mandatory_integration_scenario(self):
        start_time = datetime(2026, 9, 6, 9, 0, 0, tzinfo=dt_timezone.utc)
        count_time = datetime(2026, 9, 6, 11, 0, 0, tzinfo=dt_timezone.utc)
        finalize_time = datetime(2026, 9, 6, 12, 0, 0, tzinfo=dt_timezone.utc)

        sess = self._create_session(self.store1, started_at=start_time)
        product_a = Product.objects.create(
            name="Product A",
            sku="PRD-A",
            barcode="4780009999",
            status=Product.ProductStatus.ACTIVE,
        )
        ProductBatch.objects.create(
            store=self.store1,
            product=product_a,
            quantity=Decimal("10"),
            purchase_price=Decimal("10000.00"),
            selling_price=Decimal("15000.00"),
        )

        # 1. Startdagi kutilgan qoldiq
        InventorySnapshot.objects.create(
            session=sess,
            store=self.store1,
            product=product_a,
            expected_quantity=Decimal("10.00"),
        )

        # 2. Sessiya davridagi operatsion harakatlar (StockAllocation orqali)
        lot = StockLot.objects.create(
            store=self.store1,
            product=product_a,
            lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=Decimal("10.00"),
            remaining_quantity=Decimal("6.00"),
            purchase_price=Decimal("10000.00"),
        )

        sale = Sale.objects.create(
            store=self.store1, seller=self.admin, total_amount=Decimal("30000.00"), paid_amount=Decimal("30000.00"), status=Sale.Status.PAID,
        )
        sale_item = SaleItem.objects.create(
            sale=sale, product=product_a, quantity=Decimal("2.00"), unit_price=Decimal("15000.00"), total_price=Decimal("30000.00"),
        )
        st = StockTransfer.objects.create(from_store=self.store1, to_store=self.store2, status=StockTransfer.Status.APPROVED, created_by=self.admin)
        transfer_item = StockTransferItem.objects.create(stock_transfer=st, product=product_a, quantity=Decimal("1.00"), purchase_price=Decimal("10000.00"), selling_price=Decimal("15000.00"))
        wo = WriteOff.objects.create(store=self.store1, created_by=self.admin, reason=WriteOff.Reason.DAMAGED)
        write_off_item = WriteOffItem.objects.create(write_off=wo, product=product_a, quantity=Decimal("1.00"), purchase_price=Decimal("10000.00"), selling_price=Decimal("15000.00"))

        # SALE = 2
        self._create_alloc(
            lot=lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("10000.00"),
            sale_item=sale_item,
            created_at=datetime(2026, 9, 6, 9, 30, 0, tzinfo=dt_timezone.utc),
        )

        # TRANSFER_OUT = 1
        self._create_alloc(
            lot=lot,
            movement_type=StockAllocation.MovementType.TRANSFER_OUT,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("1.00"),
            unit_cost=Decimal("10000.00"),
            transfer_item=transfer_item,
            created_at=datetime(2026, 9, 6, 10, 0, 0, tzinfo=dt_timezone.utc),
        )

        # WRITE_OFF = 1
        self._create_alloc(
            lot=lot,
            movement_type=StockAllocation.MovementType.WRITE_OFF,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("1.00"),
            unit_cost=Decimal("10000.00"),
            write_off_item=write_off_item,
            created_at=datetime(2026, 9, 6, 10, 30, 0, tzinfo=dt_timezone.utc),
        )

        # 3. Sanoq: Count = 6
        InventoryCount.objects.create(
            session=sess,
            product=product_a,
            counted_quantity=Decimal("6.00"),
            status=InventoryCount.Status.PENDING,
            is_check=True,
            counted_at=count_time,
        )

        # 4. Finalize da yaratilgan INVENTORY_SHORTAGE allocation (shortage = 4)
        self._create_alloc(
            lot=lot,
            inventory_session=sess,
            movement_type=StockAllocation.MovementType.INVENTORY_SHORTAGE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("4.00"),
            unit_cost=Decimal("10000.00"),
            created_at=finalize_time,
        )

        # 5. Hisobotni generate qilish
        data = ReportBuilderService.generate({
            "report_type": "inventory_results",
            "session_id": str(sess.id),
        }, self.admin)

        self.assertEqual(len(data["rows"]), 1)
        row = data["rows"][0]

        # TALAB QILINGAN ANIQ KO'RSATKICHLAR:
        self.assertEqual(row["expected_qty"], 10.0, "Expected 10 bo'lishi shart")
        self.assertEqual(row["counted_qty"], 6.0, "Counted 6 bo'lishi shart")
        self.assertEqual(row["difference_qty"], -4.0, "Difference -4 bo'lishi shart")
        self.assertEqual(row["shortage_qty"], 4.0, "Shortage 4 bo'lishi shart")
        self.assertEqual(row["excess_qty"], 0.0, "Excess 0 bo'lishi shart")

        # Davriy harakatlar alohida ko'rinishi kerak:
        self.assertEqual(row["period_sold_qty"], 2.0, "Sessiya davridagi sotuv 2 bo'lishi kerak")
        self.assertEqual(row["period_transfer_out_qty"], 1.0, "Sessiya davridagi transfer out 1 bo'lishi kerak")
        self.assertEqual(row["period_write_off_qty"], 1.0, "Sessiya davridagi write off 1 bo'lishi kerak")
        self.assertEqual(row["auto_shortage_qty"], 4.0, "Avto kamomad chiqimi 4 bo'lishi kerak")
        self.assertEqual(row["auto_excess_qty"], 0.0, "Avto ortiqcha kirimi 0 bo'lishi kerak")

        # Double counting yo'qligi:
        # Final balance = 6.0 (chunki sanoqdan keyin hech qanday qo'shimcha operatsion harakat sodir bo'lmagan)
        self.assertEqual(row["final_balance"], 6.0, "Yakuniy hisobiy qoldiq 6 bo'lishi kerak")

        # Qiymatlar tekshiruvi:
        self.assertEqual(float(row["unit_purchase_cost"]), 10000.0)
        self.assertEqual(float(row["unit_sale_price"]), 15000.0)
        self.assertEqual(float(row["shortage_purchase_value"]), 40000.0)
        self.assertEqual(float(row["shortage_sale_value"]), 60000.0)

    # ──────────────────────────────────────────────────────────────────────────
    # 32. User Scenario 1: Expected=10, Counted=8, Countdan KEYIN Sale=1 -> Final Balance=7
    # ──────────────────────────────────────────────────────────────────────────
    def test_32_user_scenario_post_count_sale(self):
        start_time = datetime(2026, 9, 7, 10, 0, 0, tzinfo=dt_timezone.utc)
        count_time = datetime(2026, 9, 7, 12, 0, 0, tzinfo=dt_timezone.utc)
        sale_time = datetime(2026, 9, 7, 13, 0, 0, tzinfo=dt_timezone.utc)

        p = Product.objects.create(name="Post Count Sale Prod", sku="SCEN-01")
        batch = ProductBatch.objects.create(
            store=self.store1, product=p, quantity=Decimal("10"),
            purchase_price=Decimal("10000"), selling_price=Decimal("15000"),
        )
        lot = StockLot.objects.create(
            store=self.store1, product=p, lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=Decimal("10"), remaining_quantity=Decimal("9"), purchase_price=Decimal("10000"),
        )

        sess = self._create_session(self.store1, started_at=start_time, status=InventorySession.Status.ACTIVE)
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(
            session=sess, product=p, counted_quantity=Decimal("8"),
            status=InventoryCount.Status.PENDING, is_check=True, counted_at=count_time,
        )

        # Countdan keyin SALE = 1
        sale = Sale.objects.create(store=self.store1, seller=self.admin, total_amount=Decimal("15000"), paid_amount=Decimal("15000"), status=Sale.Status.PAID)
        si = SaleItem.objects.create(sale=sale, product=p, quantity=Decimal("1"), unit_price=Decimal("15000"), total_price=Decimal("15000"))
        self._create_alloc(
            lot=lot, movement_type=StockAllocation.MovementType.SALE, direction=StockAllocation.Direction.OUT,
            quantity=Decimal("1"), unit_cost=Decimal("10000"), sale_item=si,
            created_at=sale_time,
        )
        # InventoryMovement ham yoziladi (operational hook orqali)
        m = InventoryMovement.objects.create(session=sess, product=p, quantity=Decimal("1"), type=InventoryMovement.Type.SALE, ref_id=sale.id)
        InventoryMovement.objects.filter(id=m.id).update(created_at=sale_time)

        # 1. InventoryService.finalize() bilan yakunlash
        InventoryService.finalize(session_id=sess.id)
        batch.refresh_from_db()
        # InventoryService.finalize() da diff = final - expected = 7 - 10 = -3
        # allocate_inventory_shortage 3 dona ayiradi, lotda oldin sotilgan 1 bilan jami 4 kamayib 6 qoladi:
        self.assertEqual(batch.quantity, Decimal("6.00"))

        # 2. Report natijasini tekshirish (finalize qilingan sessiya bo'yicha)
        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["expected_qty"], 10.0)
        self.assertEqual(r["counted_qty"], 8.0)
        self.assertEqual(r["period_sold_qty"], 1.0)
        # Sanoqdan keyingi 1 dona sotuv hisobga olingan to'g'ri fizik qoldiq: 8 - 1 = 7
        self.assertEqual(r["final_balance"], 7.0, "Countdan keyingi sotuv (8 - 1 = 7) hisobga olinishi shart")

    # ──────────────────────────────────────────────────────────────────────────
    # 33. User Scenario 2: Expected=10, Countdan OLDIN Sale=1, Counted=8 -> Final Balance=8
    # ──────────────────────────────────────────────────────────────────────────
    def test_33_user_scenario_pre_count_sale(self):
        start_time = datetime(2026, 9, 8, 10, 0, 0, tzinfo=dt_timezone.utc)
        sale_time = datetime(2026, 9, 8, 11, 0, 0, tzinfo=dt_timezone.utc)
        count_time = datetime(2026, 9, 8, 12, 0, 0, tzinfo=dt_timezone.utc)

        p = Product.objects.create(name="Pre Count Sale Prod", sku="SCEN-02")
        batch = ProductBatch.objects.create(
            store=self.store1, product=p, quantity=Decimal("10"),
            purchase_price=Decimal("10000"), selling_price=Decimal("15000"),
        )
        lot = StockLot.objects.create(
            store=self.store1, product=p, lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=Decimal("10"), remaining_quantity=Decimal("9"), purchase_price=Decimal("10000"),
        )

        sess = self._create_session(self.store1, started_at=start_time, status=InventorySession.Status.ACTIVE)
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("10"))

        # Countdan OLDIN SALE = 1
        sale = Sale.objects.create(store=self.store1, seller=self.admin, total_amount=Decimal("15000"), paid_amount=Decimal("15000"), status=Sale.Status.PAID)
        si = SaleItem.objects.create(sale=sale, product=p, quantity=Decimal("1"), unit_price=Decimal("15000"), total_price=Decimal("15000"))
        self._create_alloc(
            lot=lot, movement_type=StockAllocation.MovementType.SALE, direction=StockAllocation.Direction.OUT,
            quantity=Decimal("1"), unit_cost=Decimal("10000"), sale_item=si,
            created_at=sale_time,
        )
        m = InventoryMovement.objects.create(session=sess, product=p, quantity=Decimal("1"), type=InventoryMovement.Type.SALE, ref_id=sale.id)
        InventoryMovement.objects.filter(id=m.id).update(created_at=sale_time)

        # Sanoq 12:00 da o'tkazildi (javonda 8 dona sanaldi)
        InventoryCount.objects.create(
            session=sess, product=p, counted_quantity=Decimal("8"),
            status=InventoryCount.Status.PENDING, is_check=True, counted_at=count_time,
        )

        # 1. InventoryService.finalize() bilan yakunlash
        InventoryService.finalize(session_id=sess.id)
        batch.refresh_from_db()
        # InventoryService.finalize() da diff = final - expected = 8 - 10 = -2
        # allocate_inventory_shortage 2 dona ayiradi, lotda oldin sotilgan 1 bilan jami 3 kamayib 7 qoladi:
        self.assertEqual(batch.quantity, Decimal("7.00"))

        # 2. Report natijasini tekshirish (finalize qilingan sessiya bo'yicha)
        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["expected_qty"], 10.0)
        self.assertEqual(r["counted_qty"], 8.0)
        self.assertEqual(r["period_sold_qty"], 1.0, "Sessiya davridagi sotuv informatsion tarzda 1 bo'lib ko'rinadi")
        # Sanoqdan oldingi sotuv javondagi sanoqda allaqachon aks etgan, shuning uchun qayta ayirilmaydi:
        self.assertEqual(r["final_balance"], 8.0, "Countdan oldingi sotuv qayta ayirilmasligi shart (final_balance=8)")

    # ──────────────────────────────────────────────────────────────────────────
    # 34. Zero Double-Counting: Finalize Shortage/Excess Allocations vs Period Movements
    # ──────────────────────────────────────────────────────────────────────────
    def test_34_zero_double_counting_finalize_allocations(self):
        sess = self._create_session(self.store1, started_at=datetime(2026, 9, 9, 9, 0, 0, tzinfo=dt_timezone.utc))
        p = Product.objects.create(name="No Double Count Prod", sku="NDC-01")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("50"))
        InventoryCount.objects.create(session=sess, product=p, counted_quantity=Decimal("45"), is_check=True, counted_at=datetime(2026, 9, 9, 10, 0, 0, tzinfo=dt_timezone.utc))

        lot = StockLot.objects.create(
            store=self.store1, product=p, lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=Decimal("50"), remaining_quantity=Decimal("45"), purchase_price=Decimal("10000"),
        )

        # Finalize paytida yaratilgan INVENTORY_SHORTAGE allocation
        self._create_alloc(
            lot=lot, inventory_session=sess,
            movement_type=StockAllocation.MovementType.INVENTORY_SHORTAGE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("5"), unit_cost=Decimal("10000"),
            created_at=datetime(2026, 9, 9, 11, 0, 0, tzinfo=dt_timezone.utc),
        )

        rows, _ = ReportingFoundationService.get_inventory_results_metrics(session_id=sess.id)
        r = rows[0]

        # Finalize shortage hech qachon period_sold_qty yoki period_write_off_qty ga qo'shilmasligi shart:
        self.assertEqual(r["period_sold_qty"], 0.0, "Finalize shortage period_sold ga qo'shilmasligi shart")
        self.assertEqual(r["period_write_off_qty"], 0.0, "Finalize shortage period_write_off ga qo'shilmasligi shart")
        self.assertEqual(r["period_transfer_out_qty"], 0.0)
        self.assertEqual(r["period_transfer_in_qty"], 0.0)
        self.assertEqual(r["auto_shortage_qty"], 5.0, "Aynan auto_shortage_qty da aks etishi shart")
        self.assertEqual(r["final_balance"], 45.0, "Yakuniy qoldiq 45 bo'lishi shart")
