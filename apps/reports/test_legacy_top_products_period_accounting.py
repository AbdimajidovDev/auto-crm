"""
Regression test suite for Legacy TopProductsService under Period Transactional Accounting.

Accounting Semantics:
- SaleItem:
    - sale__created_at in [start, end)
    - sale__deleted_at__isnull=True
    - sold_qty = Sum(quantity)
    - sold_revenue = Sum(total_price)
- SaleReturnItem:
    - sale_return__created_at in [start, end)
    - sale_return__sale__deleted_at__isnull=True
    - returned_qty = Sum(quantity)
    - returned_revenue = Sum(total_price)
- NET:
    - totalSold = sold_qty - returned_qty
    - totalRevenue = sold_revenue - returned_revenue
    - Products with totalSold <= 0 are excluded from results
- Store Filtering:
    - sale__store_id and sale_return__store_id
    - store_id=None consolidates across all stores
- DB-Level aggregation, filtering (totalSold > 0), sorting, and LIMIT TOP_PRODUCTS_LIMIT
- Zero Cartesian duplication
- Response Contract:
    [
        {
            "rank": 1,
            "productId": 12,
            "name": "...",
            "category": "...",
            "totalSold": Decimal(...),
            "totalRevenue": Decimal(...)
        }
    ]
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.products.models import Category, Product
from apps.reports.services.report_service import TOP_PRODUCTS_LIMIT, TopProductsService
from apps.reports.views.report_view import ReportsAPIView
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store
from apps.users.models.user import User


class LegacyTopProductsPeriodAccountingTest(TestCase):
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

        cls.cat_parts = Category.objects.create(name="Ehtiyot qismlar")
        cls.cat_oils = Category.objects.create(name="Moylar")

        cls.prod_a = Product.objects.create(
            name="Product A",
            category=cls.cat_parts,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_b = Product.objects.create(
            name="Product B",
            category=cls.cat_oils,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_c = Product.objects.create(
            name="Product C",
            category=None,  # No category -> should display "Noma'lum"
            status=Product.ProductStatus.ACTIVE,
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
    #  Test Cases
    # ─────────────────────────────────────────────────────────────

    def test_1_normal_sale(self):
        """1. Normal sale: 10 sold @ 100 -> totalSold = 10, totalRevenue = 1000"""
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"), unit_price=Decimal("100.00"))

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )

        self.assertEqual(len(res), 1)
        row = res[0]
        self.assertEqual(row["productId"], self.prod_a.id)
        self.assertEqual(row["name"], self.prod_a.name)
        self.assertEqual(row["category"], "Ehtiyot qismlar")
        self.assertEqual(row["totalSold"], Decimal("10.00"))
        self.assertEqual(row["totalRevenue"], Decimal("1000.00"))

    def test_2_same_period_partial_return(self):
        """2. Same-period partial return: 10 sold @ 100, 4 returned @ 100 -> totalSold = 6, totalRevenue = 600"""
        sale, item = self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"), unit_price=Decimal("100.00"))
        self._create_return(sale, item, self.prod_a, qty=Decimal("4.00"), refund_amount=Decimal("400.00"))

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )

        self.assertEqual(len(res), 1)
        row = res[0]
        self.assertEqual(row["totalSold"], Decimal("6.00"))
        self.assertEqual(row["totalRevenue"], Decimal("600.00"))

    def test_3_same_period_full_return_product_excluded(self):
        """3. Same-period full return: 10 sold, 10 returned -> net = 0 -> excluded from results"""
        sale, item = self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"))
        self._create_return(sale, item, self.prod_a, qty=Decimal("10.00"))

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )

        self.assertEqual(len(res), 0)

    def test_4_cross_period_return_september_and_october(self):
        """
        4. Cross-period return:
           Sep 30: Sale of 10 @ 100 (totalSold = 10, totalRevenue = 1000)
           Oct 1: Return of 4 @ 100
           Sep report: totalSold = 10, totalRevenue = 1000 (retrospectively unchanged)
           Oct report (without other sales): netSold = -4 <= 0 -> product excluded
        """
        sep_dt = datetime(2026, 9, 30, 15, 0, tzinfo=dt_timezone.utc)
        oct_dt = datetime(2026, 10, 1, 10, 0, tzinfo=dt_timezone.utc)

        sale, item = self._create_sale(
            self.store_a, self.prod_a, qty=Decimal("10.00"), unit_price=Decimal("100.00"), created_at=sep_dt
        )
        self._create_return(
            sale, item, self.prod_a, qty=Decimal("4.00"), refund_amount=Decimal("400.00"), created_at=oct_dt
        )

        # September report: Sale unchanged
        res_sep = TopProductsService.get(
            date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 30),
            store_id=self.store_a.id,
        )
        self.assertEqual(len(res_sep), 1)
        self.assertEqual(res_sep[0]["totalSold"], Decimal("10.00"))
        self.assertEqual(res_sep[0]["totalRevenue"], Decimal("1000.00"))

        # October report: Only return exists, net = -4 <= 0 -> excluded
        res_oct = TopProductsService.get(
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 31),
            store_id=self.store_a.id,
        )
        self.assertEqual(len(res_oct), 0)

    def test_5_cross_period_return_with_october_sale(self):
        """
        5. Cross-period return + October sale:
           Sep 30: Sale of 10 @ 100
           Oct 5: Sale of 5 @ 100
           Oct 10: Return of 3 @ 100 (from Sep sale)
           Oct report: netSold = 5 - 3 = 2, netRevenue = 500 - 300 = 200
        """
        sep_dt = datetime(2026, 9, 30, 15, 0, tzinfo=dt_timezone.utc)
        oct_sale_dt = datetime(2026, 10, 5, 10, 0, tzinfo=dt_timezone.utc)
        oct_ret_dt = datetime(2026, 10, 10, 12, 0, tzinfo=dt_timezone.utc)

        sep_sale, sep_item = self._create_sale(
            self.store_a, self.prod_a, qty=Decimal("10.00"), unit_price=Decimal("100.00"), created_at=sep_dt
        )
        self._create_sale(
            self.store_a, self.prod_a, qty=Decimal("5.00"), unit_price=Decimal("100.00"), created_at=oct_sale_dt
        )
        self._create_return(
            sep_sale, sep_item, self.prod_a, qty=Decimal("3.00"), refund_amount=Decimal("300.00"), created_at=oct_ret_dt
        )

        res_oct = TopProductsService.get(
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 31),
            store_id=self.store_a.id,
        )
        self.assertEqual(len(res_oct), 1)
        self.assertEqual(res_oct[0]["totalSold"], Decimal("2.00"))
        self.assertEqual(res_oct[0]["totalRevenue"], Decimal("200.00"))

    def test_6_soft_deleted_sale_excluded(self):
        """6. Soft-deleted sale (deleted_at is set) must be excluded from results"""
        deleted_dt = datetime(2026, 1, 16, 10, 0, tzinfo=dt_timezone.utc)
        self._create_sale(
            self.store_a, self.prod_a, qty=Decimal("10.00"), deleted_at=deleted_dt
        )

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )
        self.assertEqual(len(res), 0)

    def test_7_soft_deleted_sale_return_excluded(self):
        """7. Return of a soft-deleted sale must be excluded, not reducing other active sales"""
        deleted_dt = datetime(2026, 1, 16, 10, 0, tzinfo=dt_timezone.utc)
        # Active sale: 5 sold
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("5.00"))

        # Deleted sale: 10 sold, with return of 5
        del_sale, del_item = self._create_sale(
            self.store_a, self.prod_a, qty=Decimal("10.00"), deleted_at=deleted_dt
        )
        self._create_return(del_sale, del_item, self.prod_a, qty=Decimal("5.00"))

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )
        # Deleted sale's return should NOT subtract from active sale of 5
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["totalSold"], Decimal("5.00"))

    def test_8_store_isolation(self):
        """8. Store isolation: sales in Store A do not show up when filtering Store B"""
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"))

        res_b = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_b.id,
        )
        self.assertEqual(len(res_b), 0)

        res_a = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )
        self.assertEqual(len(res_a), 1)
        self.assertEqual(res_a[0]["productId"], self.prod_a.id)

    def test_9_all_store_consolidation(self):
        """9. When store_id=None, sales across all stores are consolidated per product"""
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("6.00"), unit_price=Decimal("100.00"))
        self._create_sale(self.store_b, self.prod_a, qty=Decimal("4.00"), unit_price=Decimal("100.00"))

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=None,
        )
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["totalSold"], Decimal("10.00"))
        self.assertEqual(res[0]["totalRevenue"], Decimal("1000.00"))

    def test_10_multiple_sales_and_returns_no_cartesian_duplication(self):
        """
        10. Zero Cartesian duplication:
            2 sales of 5 each (total 10 sold, 1000 revenue)
            2 returns of 1 each (total 2 returned, 200 refund)
            Expected: netSold = 8, netRevenue = 800 (not 10*2 or other cross multiplication)
        """
        sale1, item1 = self._create_sale(self.store_a, self.prod_a, qty=Decimal("5.00"), unit_price=Decimal("100.00"))
        sale2, item2 = self._create_sale(self.store_a, self.prod_a, qty=Decimal("5.00"), unit_price=Decimal("100.00"))

        self._create_return(sale1, item1, self.prod_a, qty=Decimal("1.00"), refund_amount=Decimal("100.00"))
        self._create_return(sale2, item2, self.prod_a, qty=Decimal("1.00"), refund_amount=Decimal("100.00"))

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["totalSold"], Decimal("8.00"))
        self.assertEqual(res[0]["totalRevenue"], Decimal("800.00"))

    def test_11_ranking_order(self):
        """11. Ranking: Products ordered by -totalSold, with ranks 1, 2, 3..."""
        # prod_a: net 10
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"))
        # prod_b: net 25
        self._create_sale(self.store_a, self.prod_b, qty=Decimal("25.00"))
        # prod_c: net 5
        self._create_sale(self.store_a, self.prod_c, qty=Decimal("5.00"))

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )

        self.assertEqual(len(res), 3)
        self.assertEqual([r["productId"] for r in res], [self.prod_b.id, self.prod_a.id, self.prod_c.id])
        self.assertEqual([r["rank"] for r in res], [1, 2, 3])
        self.assertEqual([r["totalSold"] for r in res], [Decimal("25.00"), Decimal("10.00"), Decimal("5.00")])

    def test_12_limit_top_products_limit_5(self):
        """12. Output is capped at TOP_PRODUCTS_LIMIT = 5"""
        extra_products = [
            Product.objects.create(name=f"Extra Prod {i}", status=Product.ProductStatus.ACTIVE)
            for i in range(1, 6)
        ]
        # prod_a has 50
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("50.00"))
        # prod_b has 40
        self._create_sale(self.store_a, self.prod_b, qty=Decimal("40.00"))
        # extra 1 to 5 have 30, 20, 10, 5, 1
        for idx, p in enumerate(extra_products):
            self._create_sale(self.store_a, p, qty=Decimal(str((5 - idx) * 10)))

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )

        self.assertEqual(len(res), TOP_PRODUCTS_LIMIT)
        self.assertEqual(len(res), 5)
        self.assertEqual([r["rank"] for r in res], [1, 2, 3, 4, 5])

    def test_13_response_structure_and_null_category(self):
        """13. Validate dictionary keys and fallback for null category"""
        self._create_sale(self.store_a, self.prod_c, qty=Decimal("5.00"), unit_price=Decimal("50.00"))

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )

        self.assertEqual(len(res), 1)
        row = res[0]
        expected_keys = {"rank", "productId", "name", "category", "totalSold", "totalRevenue"}
        self.assertEqual(set(row.keys()), expected_keys)
        self.assertEqual(row["productId"], self.prod_c.id)
        self.assertEqual(row["name"], self.prod_c.name)
        self.assertEqual(row["category"], "Noma'lum")  # fallback when category is None
        self.assertEqual(row["totalSold"], Decimal("5.00"))
        self.assertEqual(row["totalRevenue"], Decimal("250.00"))

    def test_14_date_boundary_half_open_interval(self):
        """14. Boundary [start, end): Jan 31 23:59:59 included, Feb 1 00:00:00 excluded"""
        tz = timezone.get_current_timezone()
        jan31_end = timezone.make_aware(datetime(2026, 1, 31, 23, 59, 59), tz)
        feb01_start = timezone.make_aware(datetime(2026, 2, 1, 0, 0, 0), tz)

        self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"), created_at=jan31_end)
        self._create_sale(self.store_a, self.prod_b, qty=Decimal("20.00"), created_at=feb01_start)

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )

        # prod_a is within range, prod_b is beyond range
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["productId"], self.prod_a.id)

    def test_15_total_revenue_net_calculation(self):
        """15. totalRevenue net calculation: sold total_price minus returned total_price"""
        sale, item = self._create_sale(
            self.store_a, self.prod_a, qty=Decimal("10.00"), unit_price=Decimal("120.00")
        )
        # return 3 items with refund of 360
        self._create_return(
            sale, item, self.prod_a, qty=Decimal("3.00"), refund_amount=Decimal("360.00")
        )

        res = TopProductsService.get(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            store_id=self.store_a.id,
        )

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["totalSold"], Decimal("7.00"))
        # 1200 - 360 = 840
        self.assertEqual(res[0]["totalRevenue"], Decimal("840.00"))

    def test_16_reports_api_view_integration(self):
        """16. Integration with ReportsAPIView (GET /api/v1/reports/)"""
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"), unit_price=Decimal("100.00"))
        sale2, item2 = self._create_sale(self.store_a, self.prod_b, qty=Decimal("20.00"), unit_price=Decimal("100.00"))
        self._create_return(sale2, item2, self.prod_b, qty=Decimal("5.00"), refund_amount=Decimal("500.00"))

        view = ReportsAPIView.as_view()
        req = self.factory.get(f"/api/v1/reports/?store_id={self.store_a.id}&from=2026-01-01&to=2026-01-31")
        force_authenticate(req, user=self.admin_user)

        response = view(req)
        self.assertEqual(response.status_code, 200)
        self.assertIn("topSellingProducts", response.data)

        top_products = response.data["topSellingProducts"]
        self.assertEqual(len(top_products), 2)
        # prod_b net is 15 -> rank 1
        self.assertEqual(top_products[0]["productId"], self.prod_b.id)
        self.assertEqual(top_products[0]["rank"], 1)
        self.assertEqual(Decimal(str(top_products[0]["totalSold"])), Decimal("15.00"))
        self.assertEqual(Decimal(str(top_products[0]["totalRevenue"])), Decimal("1500.00"))

        # prod_a net is 10 -> rank 2
        self.assertEqual(top_products[1]["productId"], self.prod_a.id)
        self.assertEqual(top_products[1]["rank"], 2)
        self.assertEqual(Decimal(str(top_products[1]["totalSold"])), Decimal("10.00"))
        self.assertEqual(Decimal(str(top_products[1]["totalRevenue"])), Decimal("1000.00"))
