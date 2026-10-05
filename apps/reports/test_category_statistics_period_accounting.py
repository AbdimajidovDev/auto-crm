"""
Regression test suite for CategoryStatisticsService under Period Transactional Accounting.

Accounting Invariants:
- sold_revenue = SUM(SaleItem.total_price)
- return_revenue = SUM(SaleReturnItem.total_price)
- net_revenue = sold_revenue - return_revenue
- Filter: net_revenue > 0
- Correlated scalar subquery for returns grouped by category (Zero Cartesian multiplication)
- Mandatory cross-period return invariant: past periods remain unchanged; returns deduct in return period
- Soft-deleted sales (sale__deleted_at__isnull=True) and their returns are excluded
- DEBT sales (status=Sale.Status.DEBT) are included
- Uncategorized products (category=None) fallback to "Noma'lum"
- Store isolation and consolidated mode (store_id=None)
- Full ReportsAPIView and ExcelExportService contract compatibility
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.products.models import Category, Product
from apps.reports.services.excel_export_service import ExcelExportService
from apps.reports.services.report_service import CategoryStatisticsService, ReportService
from apps.reports.views.report_view import ReportsAPIView
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store
from apps.users.models.user import User


class CategoryStatisticsPeriodAccountingTest(TestCase):
    DEFAULT_DT = datetime(2026, 1, 15, 12, 0, tzinfo=dt_timezone.utc)

    @classmethod
    def setUpTestData(cls):
        cls.store_a = Store.objects.create(name="Store A", phone_number="+998901111111")
        cls.store_b = Store.objects.create(name="Store B", phone_number="+998902222222")

        cls.admin_user = User.objects.create(
            phone_number="+998903333333",
            email="admin@example.com",
            is_staff=True,
            is_superuser=True,
        )

        cls.cat_oils = Category.objects.create(name="Moylar")
        cls.cat_filters = Category.objects.create(name="Filtrlar")
        cls.cat_brakes = Category.objects.create(name="Tormoz")

        cls.prod_oil1 = Product.objects.create(
            name="Motor Oil 5W-40",
            sku="OIL-001",
            barcode="666600000001",
            category=cls.cat_oils,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_oil2 = Product.objects.create(
            name="Transmission Oil 75W-90",
            sku="OIL-002",
            barcode="666600000002",
            category=cls.cat_oils,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_filter = Product.objects.create(
            name="Oil Filter W712",
            sku="FLT-001",
            barcode="666600000003",
            category=cls.cat_filters,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_brake = Product.objects.create(
            name="Brake Pad Front",
            sku="BRK-001",
            barcode="666600000004",
            category=cls.cat_brakes,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_nocat = Product.objects.create(
            name="Uncategorized Accessory",
            sku="ACC-001",
            barcode="666600000005",
            category=None,
            status=Product.ProductStatus.ACTIVE,
        )

        cls.factory = APIRequestFactory()

    def setUp(self):
        cache.clear()

    def _create_sale(
        self,
        store,
        product,
        qty=Decimal("1.00"),
        unit_price=Decimal("100.00"),
        total_price=None,
        status=Sale.Status.PAID,
        payment_type="cash",
        created_at=None,
        deleted_at=None,
    ):
        if total_price is None:
            total_price = qty * unit_price

        sale = Sale.objects.create(
            store=store,
            seller=self.admin_user,
            total_amount=total_price,
            paid_amount=total_price if status == Sale.Status.PAID else Decimal("0.00"),
            status=status,
            payment_type=payment_type,
        )
        item = SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=qty,
            unit_price=unit_price,
            total_price=total_price,
        )
        dt = created_at or self.DEFAULT_DT
        Sale.objects.filter(id=sale.id).update(created_at=dt)
        if deleted_at:
            Sale.all_objects.filter(id=sale.id).update(deleted_at=deleted_at)
        sale.refresh_from_db()
        item.refresh_from_db()
        return sale, item

    def _create_return(
        self,
        sale,
        item,
        product,
        qty=Decimal("1.00"),
        unit_price=None,
        refund_amount=None,
        created_at=None,
    ):
        if unit_price is None:
            unit_price = item.unit_price
        if refund_amount is None:
            refund_amount = qty * unit_price

        sale_return = SaleReturn.objects.create(
            sale=sale,
            store=sale.store,
            seller=self.admin_user,
            total_refund=refund_amount,
        )
        ret_item = SaleReturnItem.objects.create(
            sale_return=sale_return,
            sale_item=item,
            product=product,
            quantity=qty,
            unit_price=unit_price,
            total_price=refund_amount,
        )
        dt = created_at or self.DEFAULT_DT
        SaleReturn.objects.filter(id=sale_return.id).update(created_at=dt)
        sale_return.refresh_from_db()
        ret_item.refresh_from_db()
        return sale_return, ret_item

    # ─────────────────────────────────────────────────────────────
    #  Test Cases
    # ─────────────────────────────────────────────────────────────

    def test_01_normal_sale_no_return(self):
        """1. Normal sale without returns: computes accurate revenue and percent distribution."""
        # cat_oils: prod_oil1 (200) + prod_oil2 (100) = 300
        self._create_sale(self.store_a, self.prod_oil1, qty=Decimal("2"), unit_price=Decimal("100.00"))
        self._create_sale(self.store_a, self.prod_oil2, qty=Decimal("1"), unit_price=Decimal("100.00"))
        # cat_filters: 100
        self._create_sale(self.store_a, self.prod_filter, qty=Decimal("1"), unit_price=Decimal("100.00"))

        res = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_a.id)

        # Total revenue = 400. cat_oils = 300 (75.0%), cat_filters = 100 (25.0%)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["categoryName"], "Moylar")
        self.assertEqual(res[0]["revenue"], Decimal("300.00"))
        self.assertEqual(res[0]["percent"], 75.0)

        self.assertEqual(res[1]["categoryName"], "Filtrlar")
        self.assertEqual(res[1]["revenue"], Decimal("100.00"))
        self.assertEqual(res[1]["percent"], 25.0)

    def test_02_partial_return_same_period(self):
        """2. Partial return in the same period: net_revenue = sold_revenue - return_revenue."""
        # cat_oils: sold 1000, returned 300 -> net 700
        sale, item = self._create_sale(self.store_a, self.prod_oil1, qty=Decimal("10"), unit_price=Decimal("100.00"))
        self._create_return(sale, item, self.prod_oil1, qty=Decimal("3"), refund_amount=Decimal("300.00"))

        # cat_filters: sold 300, no return -> net 300
        self._create_sale(self.store_a, self.prod_filter, qty=Decimal("3"), unit_price=Decimal("100.00"))

        res = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_a.id)

        # Total = 1000. cat_oils = 700 (70.0%), cat_filters = 300 (30.0%)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["categoryName"], "Moylar")
        self.assertEqual(res[0]["revenue"], Decimal("700.00"))
        self.assertEqual(res[0]["percent"], 70.0)

        self.assertEqual(res[1]["categoryName"], "Filtrlar")
        self.assertEqual(res[1]["revenue"], Decimal("300.00"))
        self.assertEqual(res[1]["percent"], 30.0)

    def test_03_full_return_same_period(self):
        """3. Full return in the same period: net_revenue <= 0 is excluded from results."""
        # cat_oils: sold 500, fully returned 500 -> net 0 (excluded)
        sale, item = self._create_sale(self.store_a, self.prod_oil1, qty=Decimal("5"), unit_price=Decimal("100.00"))
        self._create_return(sale, item, self.prod_oil1, qty=Decimal("5"), refund_amount=Decimal("500.00"))

        # cat_filters: sold 400 -> net 400
        self._create_sale(self.store_a, self.prod_filter, qty=Decimal("4"), unit_price=Decimal("100.00"))

        res = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_a.id)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["categoryName"], "Filtrlar")
        self.assertEqual(res[0]["revenue"], Decimal("400.00"))
        self.assertEqual(res[0]["percent"], 100.0)

    def test_04_cross_period_return_past_period_unchanged(self):
        """4. Cross-period return: September sale (1,000,000) returned in October does NOT alter September."""
        dt_sept = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 5, 14, 0, tzinfo=dt_timezone.utc)

        sale, item = self._create_sale(
            self.store_a, self.prod_oil1, qty=Decimal("10"), unit_price=Decimal("100000.00"), created_at=dt_sept
        )
        self._create_return(
            sale, item, self.prod_oil1, qty=Decimal("10"), refund_amount=Decimal("1000000.00"), created_at=dt_oct
        )

        res_sept = CategoryStatisticsService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)

        # September must still reflect +1,000,000 revenue
        self.assertEqual(len(res_sept), 1)
        self.assertEqual(res_sept[0]["categoryName"], "Moylar")
        self.assertEqual(res_sept[0]["revenue"], Decimal("1000000.00"))
        self.assertEqual(res_sept[0]["percent"], 100.0)

    def test_05_cross_period_return_deducted_in_return_period(self):
        """5. Cross-period return is deducted in the return period from current sales."""
        dt_sept = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 5, 14, 0, tzinfo=dt_timezone.utc)

        # September sale
        sale_sept, item_sept = self._create_sale(
            self.store_a, self.prod_oil1, qty=Decimal("10"), unit_price=Decimal("100.00"), created_at=dt_sept
        )
        # October return from September sale (-400)
        self._create_return(
            sale_sept, item_sept, self.prod_oil1, qty=Decimal("4"), refund_amount=Decimal("400.00"), created_at=dt_oct
        )
        # October new sale for cat_oils (+1000)
        self._create_sale(
            self.store_a, self.prod_oil2, qty=Decimal("10"), unit_price=Decimal("100.00"), created_at=dt_oct
        )

        res_oct = CategoryStatisticsService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)

        # October net: 1000 - 400 = 600
        self.assertEqual(len(res_oct), 1)
        self.assertEqual(res_oct[0]["categoryName"], "Moylar")
        self.assertEqual(res_oct[0]["revenue"], Decimal("600.00"))
        self.assertEqual(res_oct[0]["percent"], 100.0)

    def test_06_return_only_category_excluded_when_net_revenue_le_zero(self):
        """6. Return-only category (returns > sales or no sales) is excluded because net_revenue <= 0."""
        dt_sept = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 5, 14, 0, tzinfo=dt_timezone.utc)

        # September sale for cat_oils
        sale_sept, item_sept = self._create_sale(
            self.store_a, self.prod_oil1, qty=Decimal("10"), unit_price=Decimal("100.00"), created_at=dt_sept
        )
        # October full return of cat_oils (-1000)
        self._create_return(
            sale_sept, item_sept, self.prod_oil1, qty=Decimal("10"), refund_amount=Decimal("1000.00"), created_at=dt_oct
        )
        # October sale only for cat_filters (+500)
        self._create_sale(
            self.store_a, self.prod_filter, qty=Decimal("5"), unit_price=Decimal("100.00"), created_at=dt_oct
        )

        res_oct = CategoryStatisticsService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)

        # cat_oils had 0 sales and 1000 return -> excluded. Only cat_filters appears.
        self.assertEqual(len(res_oct), 1)
        self.assertEqual(res_oct[0]["categoryName"], "Filtrlar")
        self.assertEqual(res_oct[0]["revenue"], Decimal("500.00"))
        self.assertEqual(res_oct[0]["percent"], 100.0)

    def test_07_deleted_sale_excluded(self):
        """7. Soft-deleted sale (deleted_at IS NOT NULL) is completely excluded from category statistics."""
        dt = datetime(2026, 1, 10, 10, 0, tzinfo=dt_timezone.utc)
        del_dt = datetime(2026, 1, 12, 10, 0, tzinfo=dt_timezone.utc)

        # Deleted sale for cat_oils
        self._create_sale(
            self.store_a, self.prod_oil1, qty=Decimal("10"), unit_price=Decimal("100.00"), created_at=dt, deleted_at=del_dt
        )
        # Active sale for cat_filters
        self._create_sale(
            self.store_a, self.prod_filter, qty=Decimal("5"), unit_price=Decimal("100.00"), created_at=dt
        )

        res = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_a.id)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["categoryName"], "Filtrlar")
        self.assertEqual(res[0]["revenue"], Decimal("500.00"))

    def test_08_return_belonging_to_deleted_sale_excluded(self):
        """8. Return belonging to a soft-deleted sale is excluded and does not deduct active sales."""
        dt = datetime(2026, 1, 10, 10, 0, tzinfo=dt_timezone.utc)
        del_dt = datetime(2026, 1, 12, 10, 0, tzinfo=dt_timezone.utc)

        # Active sale for cat_oils (+500)
        self._create_sale(self.store_a, self.prod_oil1, qty=Decimal("5"), unit_price=Decimal("100.00"), created_at=dt)

        # Deleted sale for cat_oils (+500) and its return (-500)
        del_sale, del_item = self._create_sale(
            self.store_a, self.prod_oil2, qty=Decimal("5"), unit_price=Decimal("100.00"), created_at=dt, deleted_at=del_dt
        )
        self._create_return(
            del_sale, del_item, self.prod_oil2, qty=Decimal("5"), refund_amount=Decimal("500.00"), created_at=dt
        )

        res = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_a.id)

        # Return from deleted sale must NOT deduct from the active sale
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["categoryName"], "Moylar")
        self.assertEqual(res[0]["revenue"], Decimal("500.00"))

    def test_09_store_isolation(self):
        """9. Store isolation: Store A and Store B do not bleed into each other."""
        # Store A: cat_oils (1000)
        self._create_sale(self.store_a, self.prod_oil1, qty=Decimal("10"), unit_price=Decimal("100.00"))
        # Store B: cat_filters (500)
        self._create_sale(self.store_b, self.prod_filter, qty=Decimal("5"), unit_price=Decimal("100.00"))

        res_a = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_a.id)
        self.assertEqual(len(res_a), 1)
        self.assertEqual(res_a[0]["categoryName"], "Moylar")
        self.assertEqual(res_a[0]["revenue"], Decimal("1000.00"))

        res_b = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_b.id)
        self.assertEqual(len(res_b), 1)
        self.assertEqual(res_b[0]["categoryName"], "Filtrlar")
        self.assertEqual(res_b[0]["revenue"], Decimal("500.00"))

    def test_10_consolidated_stores(self):
        """10. Consolidated stores (store_id=None): aggregates all stores accurately."""
        # Store A: cat_oils (600)
        self._create_sale(self.store_a, self.prod_oil1, qty=Decimal("6"), unit_price=Decimal("100.00"))
        # Store B: cat_oils (400)
        self._create_sale(self.store_b, self.prod_oil2, qty=Decimal("4"), unit_price=Decimal("100.00"))

        res = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), None)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["categoryName"], "Moylar")
        self.assertEqual(res[0]["revenue"], Decimal("1000.00"))
        self.assertEqual(res[0]["percent"], 100.0)

    def test_11_debt_sale_included(self):
        """11. Sale with status=DEBT is included in category statistics (previously excluded by legacy bug)."""
        self._create_sale(
            self.store_a,
            self.prod_oil1,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            status=Sale.Status.DEBT,
            payment_type="debt",
        )

        res = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_a.id)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["categoryName"], "Moylar")
        self.assertEqual(res[0]["revenue"], Decimal("1000.00"))

    def test_12_uncategorized_product_handled_as_nomalum(self):
        """12. Uncategorized products (category=None) correlate via COALESCE(category_id, 0) and display as 'Noma\'lum'."""
        sale, item = self._create_sale(
            self.store_a, self.prod_nocat, qty=Decimal("10"), unit_price=Decimal("50.00")
        )
        self._create_return(
            sale, item, self.prod_nocat, qty=Decimal("2"), refund_amount=Decimal("100.00")
        )

        res = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_a.id)

        # Net: 500 - 100 = 400
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["categoryName"], "Noma'lum")
        self.assertEqual(res[0]["revenue"], Decimal("400.00"))
        self.assertEqual(res[0]["percent"], 100.0)

    def test_13_cartesian_multiplication_prevention(self):
        """13. Cartesian multiplication prevention: multiple items in same category and multiple returns do not multiply."""
        sale1, item1 = self._create_sale(
            self.store_a, self.prod_oil1, qty=Decimal("10"), unit_price=Decimal("100.00")
        )
        sale2, item2 = self._create_sale(
            self.store_a, self.prod_oil2, qty=Decimal("10"), unit_price=Decimal("100.00")
        )

        # Two distinct returns for item1
        self._create_return(sale1, item1, self.prod_oil1, qty=Decimal("2"), refund_amount=Decimal("200.00"))
        self._create_return(sale1, item1, self.prod_oil1, qty=Decimal("3"), refund_amount=Decimal("300.00"))

        res = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_a.id)

        # Total sold = 2000. Total returned = 500. Net = 1500.
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["categoryName"], "Moylar")
        self.assertEqual(res[0]["revenue"], Decimal("1500.00"))

    def test_14_percent_calculation_and_ordering(self):
        """14. Categories are ordered by revenue DESC and percent values are rounded to 1 decimal place."""
        # cat_oils: 500 (50.0%)
        self._create_sale(self.store_a, self.prod_oil1, qty=Decimal("5"), unit_price=Decimal("100.00"))
        # cat_filters: 300 (30.0%)
        self._create_sale(self.store_a, self.prod_filter, qty=Decimal("3"), unit_price=Decimal("100.00"))
        # cat_brakes: 200 (20.0%)
        self._create_sale(self.store_a, self.prod_brake, qty=Decimal("2"), unit_price=Decimal("100.00"))

        res = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_a.id)

        self.assertEqual(len(res), 3)
        self.assertEqual([r["categoryName"] for r in res], ["Moylar", "Filtrlar", "Tormoz"])
        self.assertEqual([r["revenue"] for r in res], [Decimal("500.00"), Decimal("300.00"), Decimal("200.00")])
        self.assertEqual([r["percent"] for r in res], [50.0, 30.0, 20.0])
        self.assertAlmostEqual(sum(r["percent"] for r in res), 100.0, places=1)

    def test_15_reports_api_view_and_excel_export_contract(self):
        """15. End-to-end integration: ReportsAPIView returns categoryStatistics and ExcelExportService formats correctly."""
        self._create_sale(self.store_a, self.prod_oil1, qty=Decimal("10"), unit_price=Decimal("100.00"))
        self._create_sale(self.store_a, self.prod_filter, qty=Decimal("5"), unit_price=Decimal("100.00"))

        # Test API View
        view = ReportsAPIView.as_view()
        req = self.factory.get(f"/api/v1/reports/?store_id={self.store_a.id}&from=2026-01-01&to=2026-01-31")
        force_authenticate(req, user=self.admin_user)

        response = view(req)
        self.assertEqual(response.status_code, 200)
        self.assertIn("categoryStatistics", response.data)

        cat_stats = response.data["categoryStatistics"]
        self.assertEqual(len(cat_stats), 2)
        for row in cat_stats:
            self.assertIn("categoryName", row)
            self.assertIn("revenue", row)
            self.assertIn("percent", row)

        # Test Excel Export
        full_data = ReportService.get({
            "store_id": str(self.store_a.id),
            "from": "2026-01-01",
            "to": "2026-01-31",
            "filter": "custom",
        })
        meta = {"period": "01.01.2026 — 31.01.2026", "store": "Store A", "generated": "01.01.2026 12:00"}
        wb_buf = ExcelExportService.generate_report(full_data, meta)
        self.assertGreater(wb_buf.getbuffer().nbytes, 0)

    def test_16_date_boundary_half_open_interval(self):
        """16. Half-open interval [start, end): Jan 31 23:59:59 is included, Feb 1 00:00:00 is excluded."""
        tz = timezone.get_current_timezone()
        jan31_end = timezone.make_aware(datetime(2026, 1, 31, 23, 59, 59), tz)
        feb01_start = timezone.make_aware(datetime(2026, 2, 1, 0, 0, 0), tz)

        self._create_sale(self.store_a, self.prod_oil1, qty=Decimal("10"), unit_price=Decimal("100.00"), created_at=jan31_end)
        self._create_sale(self.store_a, self.prod_filter, qty=Decimal("5"), unit_price=Decimal("100.00"), created_at=feb01_start)

        res = CategoryStatisticsService.get(date(2026, 1, 1), date(2026, 1, 31), self.store_a.id)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["categoryName"], "Moylar")
        self.assertEqual(res[0]["revenue"], Decimal("1000.00"))
