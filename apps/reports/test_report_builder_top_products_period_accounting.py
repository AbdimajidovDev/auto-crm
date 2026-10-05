"""
Comprehensive regression test suite for ReportBuilder top_products under Period Transactional Accounting.

Accounting Semantics:
- SaleItem:
    - sale__created_at in [start, end)
    - sale__deleted_at__isnull=True
    - sold_qty = SUM(quantity)
    - sold_revenue = SUM(total_price)
    - sold_cost = SUM(quantity * COALESCE(purchase_price, 0))
- SaleReturnItem:
    - sale_return__created_at in [start, end)
    - sale_return__sale__deleted_at__isnull=True
    - ret_qty = SUM(quantity)
    - ret_revenue = SUM(total_price)
    - ret_cost = SUM(quantity * COALESCE(sale_item.purchase_price, 0))
- NET:
    - net_quantity = sold_qty - ret_qty
    - net_revenue = sold_revenue - ret_revenue
    - net_cost = sold_cost - ret_cost
    - net_profit = net_revenue - net_cost
    - Filter: net_quantity > 0
- Correlated scalar subqueries for returns (Zero Cartesian multiplication)
- Mandatory cross-period return invariant: past periods remain unchanged
- Full API integration: Generate API and Export API (Excel / CSV)
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.contract.models import StockEntry, StockEntryItem, Supplier
from apps.products.models import Category, Product
from apps.reports.services.report_builder import _build_top_products
from apps.reports.views.report_builder_view import (
    ReportBuilderExportAPIView,
    ReportBuilderGenerateAPIView,
)
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store
from apps.users.models.user import User


class ReportBuilderTopProductsPeriodAccountingTest(TestCase):
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

        cls.cat_brakes = Category.objects.create(name="Tormoz tizimi")
        cls.cat_filters = Category.objects.create(name="Filtrlar")

        cls.sup_brembo = Supplier.objects.create(name="Brembo LLC", phone_number="+998901110001")
        cls.sup_bosch = Supplier.objects.create(name="Bosch Auto", phone_number="+998901110002")

        cls.prod_pad = Product.objects.create(
            name="Brake Pad",
            sku="BP-001",
            barcode="777700000001",
            category=cls.cat_brakes,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_disc = Product.objects.create(
            name="Brake Disc",
            sku="BD-002",
            barcode="777700000002",
            category=cls.cat_brakes,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_filter = Product.objects.create(
            name="Oil Filter",
            sku="OF-003",
            barcode="777700000003",
            category=cls.cat_filters,
            status=Product.ProductStatus.ACTIVE,
        )

        # StockEntry to establish supplier mapping and supplier filtering
        entry1 = StockEntry.objects.create(supplier=cls.sup_brembo, store=cls.store_a)
        StockEntryItem.objects.create(
            entry=entry1,
            product=cls.prod_pad,
            quantity=Decimal("100"),
            purchase_price=Decimal("70.00"),
            selling_price=Decimal("100.00"),
        )
        StockEntryItem.objects.create(
            entry=entry1,
            product=cls.prod_disc,
            quantity=Decimal("50"),
            purchase_price=Decimal("150.00"),
            selling_price=Decimal("200.00"),
        )

        entry2 = StockEntry.objects.create(supplier=cls.sup_bosch, store=cls.store_a)
        StockEntryItem.objects.create(
            entry=entry2,
            product=cls.prod_filter,
            quantity=Decimal("80"),
            purchase_price=Decimal("30.00"),
            selling_price=Decimal("50.00"),
        )

        cls.factory = APIRequestFactory()

    def setUp(self):
        cache.clear()

    def _create_sale(
        self,
        store,
        product,
        qty=Decimal("10.00"),
        unit_price=Decimal("100.00"),
        purchase_price=Decimal("60.00"),
        created_at=None,
        deleted_at=None,
    ):
        sale = Sale.objects.create(
            store=store,
            seller=self.admin_user,
            total_amount=qty * unit_price,
            paid_amount=qty * unit_price,
            status=Sale.Status.PAID,
        )
        target_dt = created_at or self.DEFAULT_DT
        Sale.objects.filter(id=sale.id).update(created_at=target_dt, deleted_at=deleted_at)
        sale.refresh_from_db()

        item = SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=qty,
            unit_price=unit_price,
            purchase_price=purchase_price,
            total_price=qty * unit_price,
        )
        return sale, item

    def _create_return(
        self,
        sale,
        item,
        product,
        qty=Decimal("4.00"),
        refund_amount=None,
        created_at=None,
        store=None,
    ):
        if refund_amount is None:
            refund_amount = qty * item.unit_price

        ret = SaleReturn.objects.create(
            sale=sale,
            store=store or sale.store,
            seller=self.admin_user,
            total_refund=refund_amount,
        )
        target_dt = created_at or self.DEFAULT_DT
        SaleReturn.objects.filter(id=ret.id).update(created_at=target_dt)
        ret.refresh_from_db()

        ret_item = SaleReturnItem.objects.create(
            sale_return=ret,
            sale_item=item,
            product=product,
            quantity=qty,
            unit_price=item.unit_price,
            total_price=refund_amount,
        )
        return ret, ret_item

    # ─────────────────────────────────────────────────────────────
    #  Core Accounting Test Cases
    # ─────────────────────────────────────────────────────────────

    def test_1_normal_sale(self):
        """1. Normal sale: 10 @ 100 with cost 60 -> sold_qty=10, revenue=1000, profit=400"""
        self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10.00"), unit_price=Decimal("100.00"), purchase_price=Decimal("60.00")
        )

        params = {"from": "2026-01-01", "to": "2026-01-31"}
        columns, rows, _, summary = _build_top_products(params, self.store_a.id)

        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["name"], self.prod_pad.name)
        self.assertEqual(r["quantity"], Decimal("10.00"))
        self.assertEqual(r["revenue"], "1000.00")
        self.assertEqual(r["profit"], "400.00")

    def test_2_partial_return(self):
        """2. Partial return: 10 sold @ 100 (cost 60), 4 returned @ 100 (cost reversal 60) -> qty=6, rev=600, profit=240"""
        sale, item = self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10.00"), unit_price=Decimal("100.00"), purchase_price=Decimal("60.00")
        )
        self._create_return(sale, item, self.prod_pad, qty=Decimal("4.00"), refund_amount=Decimal("400.00"))

        params = {"from": "2026-01-01", "to": "2026-01-31"}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)

        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["quantity"], Decimal("6.00"))
        self.assertEqual(r["revenue"], "600.00")
        self.assertEqual(r["profit"], "240.00")

    def test_3_full_return(self):
        """3. Full return: 10 sold, 10 returned -> net_quantity=0 -> excluded from results"""
        sale, item = self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10.00"))
        self._create_return(sale, item, self.prod_pad, qty=Decimal("10.00"))

        params = {"from": "2026-01-01", "to": "2026-01-31"}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)

        self.assertEqual(len(rows), 0)

    def test_4_cross_period_return_mandatory(self):
        """
        4. MANDATORY CROSS-PERIOD TEST:
           September: Sale = 1,000,000, Cost = 700,000 (qty=1000, price=1000, cost=700)
           October: Full return = 1,000,000, Cost reversal = 700,000
           Expected:
             September: revenue = 1,000,000, cost = 700,000, profit = 300,000 (unchanged)
             October: net_qty <= 0 -> excluded from top products (does not artificially create positive row)
        """
        sep_dt = datetime(2026, 9, 30, 15, 0, tzinfo=dt_timezone.utc)
        oct_dt = datetime(2026, 10, 1, 10, 0, tzinfo=dt_timezone.utc)

        sale, item = self._create_sale(
            self.store_a,
            self.prod_pad,
            qty=Decimal("1000.00"),
            unit_price=Decimal("1000.00"),
            purchase_price=Decimal("700.00"),
            created_at=sep_dt,
        )
        self._create_return(
            sale,
            item,
            self.prod_pad,
            qty=Decimal("1000.00"),
            refund_amount=Decimal("1000000.00"),
            created_at=oct_dt,
        )

        # Check September: Historical performance remains completely intact
        params_sep = {"from": "2026-09-01", "to": "2026-09-30"}
        _, rows_sep, _, summary_sep = _build_top_products(params_sep, self.store_a.id)
        self.assertEqual(len(rows_sep), 1)
        self.assertEqual(rows_sep[0]["quantity"], Decimal("1000.00"))
        self.assertEqual(rows_sep[0]["revenue"], "1000000.00")
        self.assertEqual(rows_sep[0]["profit"], "300000.00")

        # Check October: No sales in Oct, only return -> net_quantity <= 0 -> excluded
        params_oct = {"from": "2026-10-01", "to": "2026-10-31"}
        _, rows_oct, _, _ = _build_top_products(params_oct, self.store_a.id)
        self.assertEqual(len(rows_oct), 0)

    def test_5_sale_and_return_in_same_period_with_other_sales(self):
        """5. Cross-period return + October sale: Oct sold 5 @ 1000 (cost 700), return 3 @ 1000 -> net=2, rev=2000, profit=600"""
        sep_dt = datetime(2026, 9, 30, 15, 0, tzinfo=dt_timezone.utc)
        oct_sale_dt = datetime(2026, 10, 5, 10, 0, tzinfo=dt_timezone.utc)
        oct_ret_dt = datetime(2026, 10, 10, 12, 0, tzinfo=dt_timezone.utc)

        sep_sale, sep_item = self._create_sale(
            self.store_a,
            self.prod_pad,
            qty=Decimal("10.00"),
            unit_price=Decimal("1000.00"),
            purchase_price=Decimal("700.00"),
            created_at=sep_dt,
        )
        self._create_sale(
            self.store_a,
            self.prod_pad,
            qty=Decimal("5.00"),
            unit_price=Decimal("1000.00"),
            purchase_price=Decimal("700.00"),
            created_at=oct_sale_dt,
        )
        self._create_return(
            sep_sale,
            sep_item,
            self.prod_pad,
            qty=Decimal("3.00"),
            refund_amount=Decimal("3000.00"),
            created_at=oct_ret_dt,
        )

        params_oct = {"from": "2026-10-01", "to": "2026-10-31"}
        _, rows_oct, _, _ = _build_top_products(params_oct, self.store_a.id)
        self.assertEqual(len(rows_oct), 1)
        # 5 - 3 = 2
        self.assertEqual(rows_oct[0]["quantity"], Decimal("2.00"))
        # 5000 - 3000 = 2000
        self.assertEqual(rows_oct[0]["revenue"], "2000.00")
        # 2 * (1000 - 700) = 600
        self.assertEqual(rows_oct[0]["profit"], "600.00")

    def test_6_deleted_sale(self):
        """6. Soft-deleted sale (deleted_at IS NOT NULL) is excluded"""
        del_dt = datetime(2026, 1, 16, 10, 0, tzinfo=dt_timezone.utc)
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10.00"), deleted_at=del_dt)

        params = {"from": "2026-01-01", "to": "2026-01-31"}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)
        self.assertEqual(len(rows), 0)

    def test_7_return_belonging_to_deleted_sale(self):
        """7. Return belonging to deleted sale must be excluded from return subtraction"""
        del_dt = datetime(2026, 1, 16, 10, 0, tzinfo=dt_timezone.utc)
        # Active sale: 5
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("5.00"))
        # Deleted sale with return: 10 sold, 5 returned
        del_sale, del_item = self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10.00"), deleted_at=del_dt)
        self._create_return(del_sale, del_item, self.prod_pad, qty=Decimal("5.00"))

        params = {"from": "2026-01-01", "to": "2026-01-31"}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], Decimal("5.00"))

    def test_8_store_isolation(self):
        """8. Store isolation: query store A does not include store B"""
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10.00"))

        params = {"from": "2026-01-01", "to": "2026-01-31"}
        _, rows_b, _, _ = _build_top_products(params, self.store_b.id)
        self.assertEqual(len(rows_b), 0)

        _, rows_a, _, _ = _build_top_products(params, self.store_a.id)
        self.assertEqual(len(rows_a), 1)

    def test_9_consolidated_multi_store_behavior(self):
        """9. When store_id=None, sales across all stores are consolidated per product"""
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("6.00"), unit_price=Decimal("100.00"))
        self._create_sale(self.store_b, self.prod_pad, qty=Decimal("4.00"), unit_price=Decimal("100.00"))

        params = {"from": "2026-01-01", "to": "2026-01-31"}
        _, rows, _, _ = _build_top_products(params, None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], Decimal("10.00"))
        self.assertEqual(rows[0]["revenue"], "1000.00")

    def test_10_cartesian_multiplication_scenario(self):
        """
        10. Zero Cartesian multiplication:
            2 sales of 5 each (total 10 sold, 1000 rev, 600 cost)
            2 returns of 1 each (total 2 returned, 200 refund, 120 cost reversal)
            Expected: net_quantity=8, net_revenue=800, net_profit=320 (not 10*2=20 or similar cross-product)
        """
        s1, i1 = self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("5.00"), unit_price=Decimal("100.00"), purchase_price=Decimal("60.00")
        )
        s2, i2 = self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("5.00"), unit_price=Decimal("100.00"), purchase_price=Decimal("60.00")
        )

        self._create_return(s1, i1, self.prod_pad, qty=Decimal("1.00"), refund_amount=Decimal("100.00"))
        self._create_return(s2, i2, self.prod_pad, qty=Decimal("1.00"), refund_amount=Decimal("100.00"))

        params = {"from": "2026-01-01", "to": "2026-01-31"}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], Decimal("8.00"))
        self.assertEqual(rows[0]["revenue"], "800.00")
        # 8 * (100 - 60) = 320
        self.assertEqual(rows[0]["profit"], "320.00")

    # ─────────────────────────────────────────────────────────────
    #  Sorting and Top-N Tests
    # ─────────────────────────────────────────────────────────────

    def test_11_sort_by_quantity(self):
        """11. sort_by=quantity: orders by net_quantity DESC"""
        # prod_pad: qty 10, profit 400
        self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10.00"), unit_price=Decimal("100.00"), purchase_price=Decimal("60.00")
        )
        # prod_disc: qty 25, profit 250
        self._create_sale(
            self.store_a, self.prod_disc, qty=Decimal("25.00"), unit_price=Decimal("100.00"), purchase_price=Decimal("90.00")
        )

        params = {"from": "2026-01-01", "to": "2026-01-31", "sort_by": "quantity"}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)
        self.assertEqual([r["name"] for r in rows], ["Brake Disc", "Brake Pad"])

    def test_12_sort_by_revenue(self):
        """12. sort_by=revenue: orders by net_revenue DESC"""
        # prod_pad: rev 1000 (qty 10 @ 100)
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10.00"), unit_price=Decimal("100.00"))
        # prod_disc: rev 4000 (qty 20 @ 200)
        self._create_sale(self.store_a, self.prod_disc, qty=Decimal("20.00"), unit_price=Decimal("200.00"))

        params = {"from": "2026-01-01", "to": "2026-01-31", "sort_by": "revenue"}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)
        self.assertEqual([r["name"] for r in rows], ["Brake Disc", "Brake Pad"])

    def test_13_sort_by_profit(self):
        """13. sort_by=profit: orders by net_profit DESC"""
        # prod_pad: rev 1000, cost 200 -> profit 800
        self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10.00"), unit_price=Decimal("100.00"), purchase_price=Decimal("20.00")
        )
        # prod_disc: rev 4000, cost 3500 -> profit 500
        self._create_sale(
            self.store_a, self.prod_disc, qty=Decimal("20.00"), unit_price=Decimal("200.00"), purchase_price=Decimal("175.00")
        )

        params = {"from": "2026-01-01", "to": "2026-01-31", "sort_by": "profit"}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)
        self.assertEqual([r["name"] for r in rows], ["Brake Pad", "Brake Disc"])

    def test_14_to_17_top_n_limits(self):
        """14-17. top_n limits: 10, 20, 50, 100"""
        # Create 15 products
        prods = [
            Product.objects.create(
                name=f"Product Extra {i}",
                barcode=f"7777000000{i+10:02d}",
                status=Product.ProductStatus.ACTIVE,
            )
            for i in range(15)
        ]
        for idx, p in enumerate(prods):
            self._create_sale(self.store_a, p, qty=Decimal(str(idx + 1)))

        # top=10
        _, rows10, _, _ = _build_top_products({"from": "2026-01-01", "to": "2026-01-31", "top": "10"}, self.store_a.id)
        self.assertEqual(len(rows10), 10)

        # top=20 (15 available)
        _, rows20, _, _ = _build_top_products({"from": "2026-01-01", "to": "2026-01-31", "top": "20"}, self.store_a.id)
        self.assertEqual(len(rows20), 15)

        # top=50
        _, rows50, _, _ = _build_top_products({"from": "2026-01-01", "to": "2026-01-31", "top": "50"}, self.store_a.id)
        self.assertEqual(len(rows50), 15)

        # top=100
        _, rows100, _, _ = _build_top_products({"from": "2026-01-01", "to": "2026-01-31", "top": "100"}, self.store_a.id)
        self.assertEqual(len(rows100), 15)

    # ─────────────────────────────────────────────────────────────
    #  Filter and Mapping Tests
    # ─────────────────────────────────────────────────────────────

    def test_18_category_filter(self):
        """18. category_id filter: only products belonging to that category"""
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10.00"))
        self._create_sale(self.store_a, self.prod_filter, qty=Decimal("15.00"))

        params = {"from": "2026-01-01", "to": "2026-01-31", "category_id": str(self.cat_brakes.id)}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], self.prod_pad.name)

    def test_19_supplier_filter(self):
        """19. supplier_id filter: only products supplied by that supplier"""
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10.00"))
        self._create_sale(self.store_a, self.prod_filter, qty=Decimal("15.00"))

        params = {"from": "2026-01-01", "to": "2026-01-31", "supplier_id": str(self.sup_bosch.id)}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], self.prod_filter.name)

    def test_20_supplier_mapping(self):
        """20. _last_supplier_map resolves supplier name for selected products"""
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10.00"))

        params = {"from": "2026-01-01", "to": "2026-01-31"}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["supplier"], "Brembo LLC")

    def test_21_response_structure_and_summary(self):
        """21. Response structure, column keys, summary cards, and margin formula"""
        self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10.00"), unit_price=Decimal("100.00"), purchase_price=Decimal("60.00")
        )

        params = {"from": "2026-01-01", "to": "2026-01-31"}
        columns, rows, extra, summary = _build_top_products(params, self.store_a.id)

        self.assertIsNone(extra)
        col_keys = [c["key"] for c in columns]
        self.assertEqual(col_keys, ["rank", "name", "sku", "category", "supplier", "quantity", "revenue", "profit"])

        row = rows[0]
        self.assertEqual(row["rank"], 1)
        self.assertEqual(row["name"], "Brake Pad")
        self.assertEqual(row["sku"], "BP-001")
        self.assertEqual(row["category"], "Tormoz tizimi")
        self.assertEqual(row["supplier"], "Brembo LLC")
        self.assertEqual(row["quantity"], Decimal("10.00"))
        self.assertEqual(row["revenue"], "1000.00")
        self.assertEqual(row["profit"], "400.00")

        # Summary cards
        summary_dict = {s["label"]: s["value"] for s in summary}
        self.assertEqual(summary_dict["Mahsulotlar"], 1)
        self.assertEqual(summary_dict["Jami sotilgan"], Decimal("10.00"))
        self.assertEqual(summary_dict["Jami daromad"], "1000.00")
        self.assertEqual(summary_dict["Sof foyda"], "400.00")
        self.assertEqual(summary_dict["Marja"], "40.0%")

    def test_22_generate_api_view_integration(self):
        """22. Integration with ReportBuilderGenerateAPIView (GET /api/v1/reports/builder/?report_type=top_products)"""
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10.00"), unit_price=Decimal("100.00"))

        view = ReportBuilderGenerateAPIView.as_view()
        req = self.factory.get(f"/api/v1/reports/builder/?report_type=top_products&from=2026-01-01&to=2026-01-31&store_id={self.store_a.id}")
        force_authenticate(req, user=self.admin_user)

        res = view(req)
        self.assertEqual(res.status_code, 200)
        self.assertIn("columns", res.data)
        self.assertIn("rows", res.data)
        self.assertIn("summary", res.data)
        self.assertEqual(len(res.data["rows"]), 1)
        self.assertEqual(res.data["rows"][0]["name"], "Brake Pad")

    def test_23_export_api_view_integration(self):
        """23. Integration with ReportBuilderExportAPIView for Excel and CSV"""
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10.00"), unit_price=Decimal("100.00"))

        view = ReportBuilderExportAPIView.as_view()

        # CSV Export
        req_csv = self.factory.get(
            f"/api/v1/reports/builder/export/?report_type=top_products&export_type=csv&from=2026-01-01&to=2026-01-31&store_id={self.store_a.id}"
        )
        force_authenticate(req_csv, user=self.admin_user)
        res_csv = view(req_csv)
        self.assertEqual(res_csv.status_code, 200)
        self.assertIn("text/csv", res_csv["Content-Type"])

        # Excel Export
        req_excel = self.factory.get(
            f"/api/v1/reports/builder/export/?report_type=top_products&export_type=excel&from=2026-01-01&to=2026-01-31&store_id={self.store_a.id}"
        )
        force_authenticate(req_excel, user=self.admin_user)
        res_excel = view(req_excel)
        self.assertEqual(res_excel.status_code, 200)
        self.assertIn("spreadsheetml", res_excel["Content-Type"])

    def test_24_null_purchase_price_behavior(self):
        """24. NULL purchase_price behaves cleanly without crashing (treated as 0 cost)"""
        self._create_sale(
            self.store_a,
            self.prod_pad,
            qty=Decimal("5.00"),
            unit_price=Decimal("100.00"),
            purchase_price=None,  # Null purchase price
        )

        params = {"from": "2026-01-01", "to": "2026-01-31"}
        _, rows, _, _ = _build_top_products(params, self.store_a.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["revenue"], "500.00")
        # cost is 0, so profit = 500
        self.assertEqual(rows[0]["profit"], "500.00")
