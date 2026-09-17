"""
Regression test suite for Product History report.
Verifies that all event types (transfer, write_off, sale, sale_return, entry,
entry_return, inventory) render without schema mismatch errors (such as missing
attributes like StockTransfer.note or WriteOff.note).
"""

from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.contract.models import (
    StockEntry,
    StockEntryItem,
    StockEntryReturn,
    StockEntryReturnItem,
    Supplier,
)
from apps.inventory.models import InventoryAdjustment, InventorySession
from apps.products.models import Brand, Category, Product, ProductBatch, ProductUnitMeasurement
from apps.reports.views.report_builder_view import ReportBuilderGenerateAPIView
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store, StoreUser
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.users.models.role import Role
from apps.users.models.user import User
from apps.writeoff.models import WriteOff, WriteOffItem


class ProductHistoryRegressionTest(TestCase):
    """
    Ensures Product History report handles:
    - Transfers (which have no note attribute on StockTransfer)
    - Write-offs (which use comment attribute on WriteOff)
    - Sales, returns, entries, entry returns, inventory adjustments
    - Pagination (page & limit)
    - Event type filtering
    """

    @classmethod
    def setUpTestData(cls):
        cls.store1 = Store.objects.create(name="Store Alpha", address="Alpha", phone_number="+998901111111")
        cls.store2 = Store.objects.create(name="Store Beta", address="Beta", phone_number="+998902222222")

        cls.role = Role.objects.create(name="Super Admin", permissions=["*"])
        cls.admin = User.objects.create(
            phone_number="+998900000999",
            email="admin_ph@reports.uz",
            full_name="Admin PH",
            is_superuser=True,
            is_staff=True,
            role=cls.role,
        )
        StoreUser.objects.create(store=cls.store1, user=cls.admin, role=cls.role)
        StoreUser.objects.create(store=cls.store2, user=cls.admin, role=cls.role)

        cls.supplier = Supplier.objects.create(name="Global Supplier", phone_number="+998903333333")
        cls.unit = ProductUnitMeasurement.objects.create(measurement="dona")
        cls.category = Category.objects.create(name="Ehtiyot qismlar")
        cls.brand = Brand.objects.create(name="Bosch")

        cls.product = Product.objects.create(
            name="Tormoz kolodkasi",
            category=cls.category,
            brand=cls.brand,
            unit_measurement=cls.unit,
            min_stock=5,
            status=Product.ProductStatus.ACTIVE,
        )

        ProductBatch.objects.create(
            product=cls.product,
            store=cls.store1,
            quantity=Decimal("50"),
            purchase_price=Decimal("100000"),
            selling_price=Decimal("150000"),
        )

        # 1. StockEntry (entry)
        cls.entry = StockEntry.objects.create(
            supplier=cls.supplier,
            store=cls.store1,
            total_amount=Decimal("1000000"),
            note="Kirim hujjati #1",
        )
        cls.entry_item = StockEntryItem.objects.create(
            entry=cls.entry,
            product=cls.product,
            quantity=Decimal("10"),
            purchase_price=Decimal("100000"),
            selling_price=Decimal("150000"),
        )

        # 2. StockTransfer (transfer)
        cls.transfer = StockTransfer.objects.create(
            from_store=cls.store1,
            to_store=cls.store2,
            status=StockTransfer.Status.APPROVED,
            created_by=cls.admin,
            approved_by=cls.admin,
            approved_at=timezone.now(),
        )
        cls.transfer_item = StockTransferItem.objects.create(
            stock_transfer=cls.transfer,
            product=cls.product,
            quantity=Decimal("3"),
            purchase_price=Decimal("100000"),
            selling_price=Decimal("150000"),
        )

        # 3. Sale (sale)
        cls.sale = Sale.objects.create(
            store=cls.store1,
            seller=cls.admin,
            total_amount=Decimal("300000"),
            paid_amount=Decimal("300000"),
            status=Sale.Status.PAID,
        )
        cls.sale_item = SaleItem.objects.create(
            sale=cls.sale,
            product=cls.product,
            quantity=Decimal("2"),
            purchase_price=Decimal("100000"),
            unit_price=Decimal("150000"),
            total_price=Decimal("300000"),
        )

        # 4. SaleReturn (sale_return)
        cls.sale_return = SaleReturn.objects.create(
            sale=cls.sale,
            store=cls.store1,
            seller=cls.admin,
            total_refund=Decimal("150000"),
            comment="Mijoz qaytardi - o'lchami to'g'ri kelmadi",
        )
        cls.sale_return_item = SaleReturnItem.objects.create(
            sale_return=cls.sale_return,
            sale_item=cls.sale_item,
            product=cls.product,
            quantity=Decimal("1"),
            unit_price=Decimal("150000"),
            total_price=Decimal("150000"),
        )

        # 5. StockEntryReturn (entry_return)
        cls.entry_return = StockEntryReturn.objects.create(
            entry=cls.entry,
            created_by=cls.admin,
            total_amount=Decimal("100000"),
            note="Zavod defekti tufayli ta'minotchiga qaytim",
        )
        cls.entry_return_item = StockEntryReturnItem.objects.create(
            stock_return=cls.entry_return,
            entry_item=cls.entry_item,
            product=cls.product,
            quantity=Decimal("1"),
            purchase_price=Decimal("100000"),
            amount=Decimal("100000"),
        )

        # 6. WriteOff (writeoff)
        cls.write_off = WriteOff.objects.create(
            store=cls.store1,
            reason=WriteOff.Reason.DAMAGED,
            comment="Omborda quti ezilib qolgan",
            total_amount=Decimal("100000"),
            created_by=cls.admin,
        )
        cls.write_off_item = WriteOffItem.objects.create(
            write_off=cls.write_off,
            product=cls.product,
            quantity=Decimal("1"),
            purchase_price=Decimal("100000"),
            selling_price=Decimal("150000"),
        )

        # 7. InventoryAdjustment (inventory)
        cls.inv_session = InventorySession.objects.create(
            store=cls.store1,
            started_by=cls.admin,
            status=InventorySession.Status.COMPLETED,
        )
        cls.inv_adj = InventoryAdjustment.objects.create(
            session=cls.inv_session,
            product=cls.product,
            difference=Decimal("2"),
        )

    def setUp(self):
        self.factory = APIRequestFactory()

    def _get_history(self, **extra_params):
        params = {
            "report_type": "product_history",
            "product_id": str(self.product.id),
        }
        params.update(extra_params)
        request = self.factory.get("/api/reports/builder/", params)
        force_authenticate(request, user=self.admin)
        view = ReportBuilderGenerateAPIView.as_view()
        return view(request)

    def test_transfer_event_renders_200_without_attribute_error(self):
        """Specifically verifies StockTransfer has no attribute error and returns note='-'."""
        resp = self._get_history(event_type="transfer")
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertEqual(data["total"], 1)
        row = data["rows"][0]
        self.assertEqual(row["event"], "O'tkazma")
        self.assertEqual(row["doc_id"], self.transfer.id)
        self.assertEqual(row["store"], "Store Alpha")
        self.assertEqual(row["to_store"], "Store Beta")
        self.assertEqual(row["quantity"], Decimal("3.00"))
        self.assertEqual(row["status"], "Tasdiqlangan")
        # In API response table, empty note renders as '-'
        self.assertEqual(row["note"], "-")

    def test_writeoff_event_renders_200_and_uses_comment_as_note(self):
        """Specifically verifies WriteOff uses comment attribute and does not crash on .note."""
        resp = self._get_history(event_type="writeoff")
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertEqual(data["total"], 1)
        row = data["rows"][0]
        self.assertEqual(row["event"], "Spisaniye")
        self.assertEqual(row["doc_id"], self.write_off.id)
        self.assertEqual(row["status"], "Buzilgan / yaroqsiz")
        self.assertEqual(row["note"], "Omborda quti ezilib qolgan")

    def test_all_seven_event_types_combined_return_200(self):
        """Verifies full timeline with all 7 distinct movement types."""
        resp = self._get_history(page=1, limit=25)
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertEqual(data["total"], 7)
        self.assertEqual(len(data["rows"]), 7)

        events_seen = {r["event"] for r in data["rows"]}
        expected_events = {
            "Kirim",
            "O'tkazma",
            "Sotuv",
            "Sotuv qaytimi",
            "Kirim qaytimi",
            "Spisaniye",
            "Inventarizatsiya",
        }
        self.assertEqual(events_seen, expected_events)

        # Check specific notes across all events
        rows_by_event = {r["event"]: r for r in data["rows"]}
        self.assertEqual(rows_by_event["Kirim"]["note"], "Kirim hujjati #1")
        self.assertEqual(rows_by_event["O'tkazma"]["note"], "-")
        self.assertEqual(rows_by_event["Sotuv"]["note"], "-")
        self.assertEqual(rows_by_event["Sotuv qaytimi"]["note"], "Mijoz qaytardi - o'lchami to'g'ri kelmadi")
        self.assertEqual(rows_by_event["Kirim qaytimi"]["note"], "Zavod defekti tufayli ta'minotchiga qaytim")
        self.assertEqual(rows_by_event["Spisaniye"]["note"], "Omborda quti ezilib qolgan")
        self.assertIn(f"Inventarizatsiya #{self.inv_session.id}", rows_by_event["Inventarizatsiya"]["note"])

    def test_pagination_on_product_history(self):
        """Tests page and limit parameters work properly."""
        resp_p1 = self._get_history(page=1, limit=3)
        self.assertEqual(resp_p1.status_code, 200)
        self.assertEqual(resp_p1.data["total"], 7)
        self.assertEqual(len(resp_p1.data["rows"]), 3)

        resp_p2 = self._get_history(page=2, limit=3)
        self.assertEqual(resp_p2.status_code, 200)
        self.assertEqual(resp_p2.data["total"], 7)
        self.assertEqual(len(resp_p2.data["rows"]), 3)

        resp_p3 = self._get_history(page=3, limit=3)
        self.assertEqual(resp_p3.status_code, 200)
        self.assertEqual(resp_p3.data["total"], 7)
        self.assertEqual(len(resp_p3.data["rows"]), 1)

        # Ensure pages return disjoint sets of doc_id/event
        p1_docs = {(r["event"], r["doc_id"]) for r in resp_p1.data["rows"]}
        p2_docs = {(r["event"], r["doc_id"]) for r in resp_p2.data["rows"]}
        p3_docs = {(r["event"], r["doc_id"]) for r in resp_p3.data["rows"]}
        self.assertEqual(len(p1_docs.intersection(p2_docs)), 0)
        self.assertEqual(len(p2_docs.intersection(p3_docs)), 0)
