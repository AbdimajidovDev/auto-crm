"""
Regression test suite for TopProducts API & Service under Period Transactional Accounting.

Accounting Semantics:
- net_sold_qty = (SaleItem.quantity in period) - (SaleReturnItem.quantity in period)
- Sale enters report period based on Sale.created_at, Sale.deleted_at IS NULL
- Return enters report period based on SaleReturn.created_at, SaleReturn.sale.deleted_at IS NULL
- Products with net_sold_qty <= 0 are excluded from results
- Cross-period returns do not alter previous sale period retrospectively
- When store_id is None, products are consolidated across permitted stores
- StoreFilterService RBAC is fully preserved
- Zero Cartesian duplication
- Response contract: {"topProducts": [{"product_id": ..., "name": ..., "total_sold": ...}]}
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.products.models import Product
from apps.reports.services.top_product_service import TopProductsService
from apps.reports.views.top_product_view import TopProductsAPIView
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store, StoreUser
from apps.users.models.user import User


class TopProductsPeriodAccountingTest(TestCase):
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
        cls.manager_user = User.objects.create(
            phone_number="+998904444444",
            email="manager@example.com",
            is_staff=True,
            is_superuser=False,
        )
        # manager only has access to store_a
        StoreUser.objects.create(
            user=cls.manager_user,
            store=cls.store_a,
            is_active=True,
        )

        cls.prod_a = Product.objects.create(name="Product A", status=Product.ProductStatus.ACTIVE)
        cls.prod_b = Product.objects.create(name="Product B", status=Product.ProductStatus.ACTIVE)
        cls.prod_c = Product.objects.create(name="Product C", status=Product.ProductStatus.ACTIVE)

        cls.factory = APIRequestFactory()

    def _create_sale(self, store, product, qty=Decimal("10.00"), created_at=None, deleted_at=None):
        sale = Sale.objects.create(
            store=store,
            seller=self.admin_user,
            total_amount=qty * Decimal("100.00"),
            paid_amount=qty * Decimal("100.00"),
            status=Sale.Status.PAID,
        )
        target_dt = created_at or self.DEFAULT_DT
        Sale.objects.filter(id=sale.id).update(created_at=target_dt, deleted_at=deleted_at)
        sale.refresh_from_db()

        item = SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=qty,
            unit_price=Decimal("100.00"),
            total_price=qty * Decimal("100.00"),
        )
        return sale, item

    def _create_return(self, sale, item, product, qty=Decimal("4.00"), created_at=None, store=None):
        ret = SaleReturn.objects.create(
            sale=sale,
            store=store or sale.store,
            seller=self.admin_user,
            total_refund=qty * Decimal("100.00"),
        )
        target_dt = created_at or self.DEFAULT_DT
        SaleReturn.objects.filter(id=ret.id).update(created_at=target_dt)
        ret.refresh_from_db()

        ret_item = SaleReturnItem.objects.create(
            sale_return=ret,
            sale_item=item,
            product=product,
            quantity=qty,
            unit_price=Decimal("100.00"),
            total_price=qty * Decimal("100.00"),
        )
        return ret, ret_item

    def test_1_normal_sale(self):
        """1. Normal sale: 10 sold -> total_sold = 10"""
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"))

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
        )

        prod_row = next((r for r in res if r["product_id"] == self.prod_a.id), None)
        self.assertIsNotNone(prod_row)
        self.assertEqual(prod_row["name"], self.prod_a.name)
        self.assertEqual(prod_row["total_sold"], Decimal("10.00"))

    def test_2_same_period_partial_return(self):
        """2. Same-period partial return: 10 sold - 4 returned = 6 net"""
        sale, item = self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"))
        self._create_return(sale, item, self.prod_a, qty=Decimal("4.00"))

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
        )

        prod_row = next((r for r in res if r["product_id"] == self.prod_a.id), None)
        self.assertIsNotNone(prod_row)
        self.assertEqual(prod_row["total_sold"], Decimal("6.00"))

    def test_3_same_period_full_return_excluded(self):
        """3. Same-period full return: 10 sold - 10 returned = 0 -> product excluded"""
        sale, item = self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"))
        self._create_return(sale, item, self.prod_a, qty=Decimal("10.00"))

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
        )

        prod_row = next((r for r in res if r["product_id"] == self.prod_a.id), None)
        self.assertIsNone(prod_row)

    def test_4_cross_period_return(self):
        """
        4. Cross-period return:
           Sep 30 sale 10 -> Sep report shows 10
           Oct 1 return 10 -> Oct report does not include product (net <= 0)
           Sep report re-checked -> still shows 10 (retrospective integrity)
        """
        dt_sep = datetime(2026, 9, 30, 15, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 1, 10, 0, tzinfo=dt_timezone.utc)

        sale, item = self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"), created_at=dt_sep)
        self._create_return(sale, item, self.prod_a, qty=Decimal("10.00"), created_at=dt_oct)

        # September report
        res_sep = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 30),
            limit=5,
        )
        row_sep = next((r for r in res_sep if r["product_id"] == self.prod_a.id), None)
        self.assertIsNotNone(row_sep)
        self.assertEqual(row_sep["total_sold"], Decimal("10.00"))

        # October report: net <= 0, must be excluded
        res_oct = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 31),
            limit=5,
        )
        row_oct = next((r for r in res_oct if r["product_id"] == self.prod_a.id), None)
        self.assertIsNone(row_oct)

    def test_5_soft_deleted_sale_excluded(self):
        """5. Soft-deleted sale is excluded from top products"""
        dt_del = datetime(2026, 1, 20, 12, 0, tzinfo=dt_timezone.utc)
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("50.00"), deleted_at=dt_del)

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
        )
        row = next((r for r in res if r["product_id"] == self.prod_a.id), None)
        self.assertIsNone(row)

    def test_6_soft_deleted_sale_return_excluded(self):
        """6. Soft-deleted sale's return is excluded and does not affect active sales"""
        dt_del = datetime(2026, 1, 20, 12, 0, tzinfo=dt_timezone.utc)
        # Active sale: 20 sold
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("20.00"))
        # Soft-deleted sale with a return of 20
        del_sale, del_item = self._create_sale(self.store_a, self.prod_a, qty=Decimal("20.00"), deleted_at=dt_del)
        self._create_return(del_sale, del_item, self.prod_a, qty=Decimal("20.00"))

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
        )
        row = next((r for r in res if r["product_id"] == self.prod_a.id), None)
        self.assertIsNotNone(row)
        # The return belonged to deleted sale, so active sale of 20 remains unaffected
        self.assertEqual(row["total_sold"], Decimal("20.00"))

    def test_7_store_isolation(self):
        """7. Store A vs Store B isolation"""
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("15.00"))
        self._create_sale(self.store_b, self.prod_b, qty=Decimal("25.00"))

        # Query Store A
        res_a = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
            store_id=self.store_a.id,
        )
        pids_a = [r["product_id"] for r in res_a]
        self.assertIn(self.prod_a.id, pids_a)
        self.assertNotIn(self.prod_b.id, pids_a)

        # Query Store B
        res_b = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
            store_id=self.store_b.id,
        )
        pids_b = [r["product_id"] for r in res_b]
        self.assertIn(self.prod_b.id, pids_b)
        self.assertNotIn(self.prod_a.id, pids_b)

    def test_8_store_rbac(self):
        """8. Store RBAC: non-superuser cannot access unauthorized store"""
        # Manager only has store_a access. Querying store_b must raise PermissionError
        with self.assertRaises(PermissionError):
            TopProductsService.get_top_products(
                user=self.manager_user,
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 31),
                limit=5,
                store_id=self.store_b.id,
            )

        # Querying without store_id shows only store_a sales for manager
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"))
        self._create_sale(self.store_b, self.prod_b, qty=Decimal("50.00"))

        res_mgr = TopProductsService.get_top_products(
            user=self.manager_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
            store_id=None,
        )
        mgr_pids = [r["product_id"] for r in res_mgr]
        self.assertIn(self.prod_a.id, mgr_pids)
        self.assertNotIn(self.prod_b.id, mgr_pids)

    def test_9_limit(self):
        """9. Limit parameter is respected"""
        prod_d = Product.objects.create(name="Product D", status=Product.ProductStatus.ACTIVE)
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("40.00"))
        self._create_sale(self.store_a, self.prod_b, qty=Decimal("30.00"))
        self._create_sale(self.store_a, self.prod_c, qty=Decimal("20.00"))
        self._create_sale(self.store_a, prod_d, qty=Decimal("10.00"))

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=2,
        )
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["product_id"], self.prod_a.id)
        self.assertEqual(res[1]["product_id"], self.prod_b.id)

    def test_10_multiple_sales_and_returns_no_cartesian_multiplication(self):
        """
        10. Multiple sales and multiple returns:
            Sale 1: 15, Sale 2: 25 -> Total sold: 40
            Return 1: 5, Return 2: 7 -> Total returns: 12
            Net: 40 - 12 = 28 (Cartesian multiplication would give 40*2 or 12*2).
        """
        s1, i1 = self._create_sale(self.store_a, self.prod_a, qty=Decimal("15.00"))
        s2, i2 = self._create_sale(self.store_a, self.prod_a, qty=Decimal("25.00"))

        self._create_return(s1, i1, self.prod_a, qty=Decimal("5.00"))
        self._create_return(s2, i2, self.prod_a, qty=Decimal("7.00"))

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
        )
        row = next(r for r in res if r["product_id"] == self.prod_a.id)
        self.assertEqual(row["total_sold"], Decimal("28.00"))

    def test_11_all_stores_consolidation(self):
        """11. When store_id=None, sales across all permitted stores are consolidated"""
        # Store A: 10 sold, Store B: 15 sold -> Consolidated net: 25
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"))
        self._create_sale(self.store_b, self.prod_a, qty=Decimal("15.00"))

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
            store_id=None,
        )
        row = next(r for r in res if r["product_id"] == self.prod_a.id)
        self.assertEqual(row["total_sold"], Decimal("25.00"))

    def test_12_response_structure(self):
        """12. Exact response structure preserved"""
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"))

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
        )
        self.assertGreater(len(res), 0)
        expected_keys = {"product_id", "name", "total_sold"}
        for item in res:
            self.assertEqual(set(item.keys()), expected_keys)
            self.assertIsInstance(item["product_id"], int)
            self.assertIsInstance(item["name"], str)
            self.assertIsInstance(item["total_sold"], Decimal)

    def test_13_zero_and_negative_net_products_excluded(self):
        """13. Zero and negative net products excluded"""
        # prod_a: 10 sold, 10 returned -> net 0
        s_a, i_a = self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"))
        self._create_return(s_a, i_a, self.prod_a, qty=Decimal("10.00"))

        # prod_b: sold in 2025, returned in Jan 2026 -> net -5
        dt_old = datetime(2025, 12, 1, 12, 0, tzinfo=dt_timezone.utc)
        s_b, i_b = self._create_sale(self.store_a, self.prod_b, qty=Decimal("5.00"), created_at=dt_old)
        self._create_return(s_b, i_b, self.prod_b, qty=Decimal("5.00"))  # Jan 2026 return

        # prod_c: 12 sold -> net 12
        self._create_sale(self.store_a, self.prod_c, qty=Decimal("12.00"))

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
        )
        pids = [r["product_id"] for r in res]
        self.assertIn(self.prod_c.id, pids)
        self.assertNotIn(self.prod_a.id, pids)
        self.assertNotIn(self.prod_b.id, pids)

    def test_14_multiple_products_ranking_order(self):
        """14. Products ordered by -net_sold_qty, product_id"""
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"))
        self._create_sale(self.store_a, self.prod_b, qty=Decimal("30.00"))
        self._create_sale(self.store_a, self.prod_c, qty=Decimal("20.00"))

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
        )
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0]["product_id"], self.prod_b.id)  # 30
        self.assertEqual(res[1]["product_id"], self.prod_c.id)  # 20
        self.assertEqual(res[2]["product_id"], self.prod_a.id)  # 10

    def test_15_date_boundary_half_open_interval(self):
        """15. Exact [start, end) half-open interval boundaries"""
        tz = timezone.get_current_timezone()
        dt_start = timezone.make_aware(datetime(2026, 1, 1, 0, 0, 0), tz)
        dt_end = timezone.make_aware(datetime(2026, 2, 1, 0, 0, 0), tz)
        dt_inside = timezone.make_aware(datetime(2026, 1, 31, 23, 59, 59), tz)

        # prod_a at start exact: INCLUDED
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("10.00"), created_at=dt_start)
        # prod_b just before end: INCLUDED
        self._create_sale(self.store_a, self.prod_b, qty=Decimal("20.00"), created_at=dt_inside)
        # prod_c at end exact: EXCLUDED (belongs to next month)
        self._create_sale(self.store_a, self.prod_c, qty=Decimal("30.00"), created_at=dt_end)

        res = TopProductsService.get_top_products(
            user=self.admin_user,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 31),
            limit=5,
        )
        pids = [r["product_id"] for r in res]
        self.assertIn(self.prod_a.id, pids)
        self.assertIn(self.prod_b.id, pids)
        self.assertNotIn(self.prod_c.id, pids)

    def test_16_api_view_endpoint(self):
        """16. TopProductsAPIView HTTP endpoint integration"""
        self._create_sale(self.store_a, self.prod_a, qty=Decimal("15.00"))
        self._create_sale(self.store_a, self.prod_b, qty=Decimal("25.00"))

        view = TopProductsAPIView.as_view()
        request = self.factory.get("/api/v1/reports/top-products/?from=2026-01-01&to=2026-01-31&limit=2")
        force_authenticate(request, user=self.admin_user)
        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("topProducts", response.data)
        self.assertEqual(len(response.data["topProducts"]), 2)
        self.assertEqual(response.data["topProducts"][0]["product_id"], self.prod_b.id)
        self.assertEqual(Decimal(str(response.data["topProducts"][0]["total_sold"])), Decimal("25.00"))
