"""
Regression test suite for Product History report.
Verifies that all event types (transfer, write_off, sale, sale_return, entry,
entry_return, inventory) render without schema mismatch errors (such as missing
attributes like StockTransfer.note or WriteOff.note).
"""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.reports.services.product_movement_report_service import ProductMovementReportService

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


class ProductHistorySummaryProfitPeriodAccountingTest(TestCase):
    """
    Regression test suite for Product History summary profit under Period Transactional Accounting.

    Formula:
    profit = net_sold_amount - net_cost_amount
    where:
    net_sold_amount = sold_amount - sale_returned_amount
    net_cost_amount = cost_amount - sale_returned_cost_amount

    Tested cases:
    1. Oddiy sale: sold=100, cost=60, profit=40
    2. Partial return: sale=100, cost=60, return revenue=40, return cost=24 -> net_sold=60, net_cost=36, profit=24
       (va return_revenue=60, return_cost=36 bo'lganda net_sold=40, net_cost=24, profit=16)
    3. Full return: sale=100, cost=60, full return -> profit=0
    4. Cross-period return:
       - Sale period: profit saqlanadi (+40)
       - Return period: transactional summary manfiy effect ko'rsatadi (-16)
    """

    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Store Test", address="Test", phone_number="+998901234500")
        cls.role = Role.objects.create(name="Admin Role", permissions=["*"])
        cls.admin = User.objects.create(
            phone_number="+998901234501",
            email="admin_summary_test@example.com",
            full_name="Admin Test",
            is_superuser=True,
            is_staff=True,
            role=cls.role,
        )
        StoreUser.objects.create(store=cls.store, user=cls.admin, role=cls.role)

        cls.supplier = Supplier.objects.create(name="Supplier Test", phone_number="+998901234502")
        cls.category = Category.objects.create(name="Kategoriya")
        cls.unit = ProductUnitMeasurement.objects.create(measurement="dona")

    def _create_product(self, name="Test Mahsulot"):
        return Product.objects.create(
            name=name,
            category=self.category,
            unit_measurement=self.unit,
            min_stock=1,
            status=Product.ProductStatus.ACTIVE,
        )

    def test_1_normal_sale(self):
        """
        1. Oddiy sale:
           sold = 100
           cost = 60
           profit = 40
        """
        prod = self._create_product("Normal Sale Product")
        # 10 dona x 10 = 100 sotuv, tannarx 6 (jami cost = 60)
        sale = Sale.objects.create(
            store=self.store, seller=self.admin,
            total_amount=Decimal("100.00"), paid_amount=Decimal("100.00"),
            status=Sale.Status.PAID,
        )
        SaleItem.objects.create(
            sale=sale, product=prod, quantity=Decimal("10"),
            unit_price=Decimal("10.00"), purchase_price=Decimal("6.00"),
            total_price=Decimal("100.00"),
        )

        service = ProductMovementReportService(prod, self.admin)
        by_store = service.build_by_store()
        totals = service.build_summary(by_store)

        self.assertEqual(totals["sold_amount"], Decimal("100.00"))
        self.assertEqual(totals["cost_amount"], Decimal("60.00"))
        self.assertEqual(totals["sale_returned_amount"], Decimal("0.00"))
        self.assertEqual(totals["sale_returned_cost_amount"], Decimal("0.00"))
        self.assertEqual(totals["net_sold_amount"], Decimal("100.00"))
        self.assertEqual(totals["net_cost_amount"], Decimal("60.00"))
        self.assertEqual(totals["profit"], Decimal("40.00"))

    def test_2_partial_return(self):
        """
        2. Partial return:
           sale = 100
           cost = 60
           return revenue = 40
           return cost = 24
           Qolgan tovar bo'yicha sof sotuv = 60, sof tannarx = 36, expected profit = 24.
           Agar 6 dona qaytsa (return revenue = 60, return cost = 36):
           sof sotuv = 40, sof tannarx = 24, expected profit = 16.
        """
        prod = self._create_product("Partial Return Product")
        sale = Sale.objects.create(
            store=self.store, seller=self.admin,
            total_amount=Decimal("100.00"), paid_amount=Decimal("100.00"),
            status=Sale.Status.PAID,
        )
        item = SaleItem.objects.create(
            sale=sale, product=prod, quantity=Decimal("10"),
            unit_price=Decimal("10.00"), purchase_price=Decimal("6.00"),
            total_price=Decimal("100.00"),
        )

        # 4 dona qaytarildi (return revenue = 40, return cost = 4 * 6 = 24)
        ret = SaleReturn.objects.create(
            sale=sale, store=self.store, seller=self.admin,
            total_refund=Decimal("40.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=ret, sale_item=item, product=prod,
            quantity=Decimal("4"), unit_price=Decimal("10.00"),
            total_price=Decimal("40.00"),
        )

        service = ProductMovementReportService(prod, self.admin)
        by_store = service.build_by_store()
        totals = service.build_summary(by_store)

        self.assertEqual(totals["sold_amount"], Decimal("100.00"))
        self.assertEqual(totals["cost_amount"], Decimal("60.00"))
        self.assertEqual(totals["sale_returned_amount"], Decimal("40.00"))
        self.assertEqual(totals["sale_returned_cost_amount"], Decimal("24.00"))
        self.assertEqual(totals["net_sold_amount"], Decimal("60.00"))
        self.assertEqual(totals["net_cost_amount"], Decimal("36.00"))
        # 60 - 36 = 24
        self.assertEqual(totals["profit"], Decimal("24.00"))

        # Endi qo'shimcha 2 dona qaytarilsa (jami 6 dona qaytdi: revenue=60, cost=36)
        SaleReturnItem.objects.create(
            sale_return=ret, sale_item=item, product=prod,
            quantity=Decimal("2"), unit_price=Decimal("10.00"),
            total_price=Decimal("20.00"),
        )
        totals_6 = service.build_summary(service.build_by_store())
        self.assertEqual(totals_6["sale_returned_amount"], Decimal("60.00"))
        self.assertEqual(totals_6["sale_returned_cost_amount"], Decimal("36.00"))
        self.assertEqual(totals_6["net_sold_amount"], Decimal("40.00"))
        self.assertEqual(totals_6["net_cost_amount"], Decimal("24.00"))
        # 40 - 24 = 16
        self.assertEqual(totals_6["profit"], Decimal("16.00"))

    def test_3_full_return(self):
        """
        3. Full return:
           sale = 100
           cost = 60
           full return (return revenue = 100, return cost = 60)
           expected profit = 0
        """
        prod = self._create_product("Full Return Product")
        sale = Sale.objects.create(
            store=self.store, seller=self.admin,
            total_amount=Decimal("100.00"), paid_amount=Decimal("100.00"),
            status=Sale.Status.PAID,
        )
        item = SaleItem.objects.create(
            sale=sale, product=prod, quantity=Decimal("10"),
            unit_price=Decimal("10.00"), purchase_price=Decimal("6.00"),
            total_price=Decimal("100.00"),
        )

        ret = SaleReturn.objects.create(
            sale=sale, store=self.store, seller=self.admin,
            total_refund=Decimal("100.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=ret, sale_item=item, product=prod,
            quantity=Decimal("10"), unit_price=Decimal("10.00"),
            total_price=Decimal("100.00"),
        )

        service = ProductMovementReportService(prod, self.admin)
        by_store = service.build_by_store()
        totals = service.build_summary(by_store)

        self.assertEqual(totals["sold_amount"], Decimal("100.00"))
        self.assertEqual(totals["cost_amount"], Decimal("60.00"))
        self.assertEqual(totals["sale_returned_amount"], Decimal("100.00"))
        self.assertEqual(totals["sale_returned_cost_amount"], Decimal("60.00"))
        self.assertEqual(totals["net_sold_amount"], Decimal("0.00"))
        self.assertEqual(totals["net_cost_amount"], Decimal("0.00"))
        self.assertEqual(totals["profit"], Decimal("0.00"))

    def test_4_cross_period_return(self):
        """
        4. Cross-period return:
           Sale periodida (Yanvar) sale profit saqlansin (+40).
           Return periodida (Fevral) transactional summary tegishli manfiy effectni ko'rsatsin (-16).
        """
        dt_sale = datetime(2026, 1, 15, 12, 0, tzinfo=dt_timezone.utc)
        dt_return = datetime(2026, 2, 10, 14, 0, tzinfo=dt_timezone.utc)

        prod = self._create_product("Cross Period Product")
        sale = Sale.objects.create(
            store=self.store, seller=self.admin,
            total_amount=Decimal("100.00"), paid_amount=Decimal("100.00"),
            status=Sale.Status.PAID,
        )
        item = SaleItem.objects.create(
            sale=sale, product=prod, quantity=Decimal("10"),
            unit_price=Decimal("10.00"), purchase_price=Decimal("6.00"),
            total_price=Decimal("100.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=dt_sale)

        ret = SaleReturn.objects.create(
            sale=sale, store=self.store, seller=self.admin,
            total_refund=Decimal("40.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=ret, sale_item=item, product=prod,
            quantity=Decimal("4"), unit_price=Decimal("10.00"),
            total_price=Decimal("40.00"),
        )
        SaleReturn.objects.filter(id=ret.id).update(created_at=dt_return)

        # 4.1. Yanvar oyi hisoboti (Sale period):
        service_jan = ProductMovementReportService(
            prod, self.admin,
            date_from=datetime(2026, 1, 1, 0, 0, tzinfo=dt_timezone.utc),
            date_to=datetime(2026, 1, 31, 23, 59, 59, tzinfo=dt_timezone.utc),
        )
        totals_jan = service_jan.build_summary(service_jan.build_by_store())
        self.assertEqual(totals_jan["sold_amount"], Decimal("100.00"))
        self.assertEqual(totals_jan["cost_amount"], Decimal("60.00"))
        self.assertEqual(totals_jan["sale_returned_amount"], Decimal("0.00"))
        self.assertEqual(totals_jan["sale_returned_cost_amount"], Decimal("0.00"))
        self.assertEqual(totals_jan["net_sold_amount"], Decimal("100.00"))
        self.assertEqual(totals_jan["net_cost_amount"], Decimal("60.00"))
        self.assertEqual(totals_jan["profit"], Decimal("40.00"))

        # 4.2. Fevral oyi hisoboti (Return period):
        service_feb = ProductMovementReportService(
            prod, self.admin,
            date_from=datetime(2026, 2, 1, 0, 0, tzinfo=dt_timezone.utc),
            date_to=datetime(2026, 2, 28, 23, 59, 59, tzinfo=dt_timezone.utc),
        )
        totals_feb = service_feb.build_summary(service_feb.build_by_store())
        self.assertEqual(totals_feb["sold_amount"], Decimal("0.00"))
        self.assertEqual(totals_feb["cost_amount"], Decimal("0.00"))
        self.assertEqual(totals_feb["sale_returned_amount"], Decimal("40.00"))
        self.assertEqual(totals_feb["sale_returned_cost_amount"], Decimal("24.00"))
        self.assertEqual(totals_feb["net_sold_amount"], Decimal("-40.00"))
        self.assertEqual(totals_feb["net_cost_amount"], Decimal("-24.00"))
        # -40 - (-24) = -16
        self.assertEqual(totals_feb["profit"], Decimal("-16.00"))

        # 4.3. Butun yil hisoboti (Umumiy jamlanma):
        service_all = ProductMovementReportService(
            prod, self.admin,
            date_from=datetime(2026, 1, 1, 0, 0, tzinfo=dt_timezone.utc),
            date_to=datetime(2026, 12, 31, 23, 59, 59, tzinfo=dt_timezone.utc),
        )
        totals_all = service_all.build_summary(service_all.build_by_store())
        self.assertEqual(totals_all["profit"], Decimal("24.00"))

