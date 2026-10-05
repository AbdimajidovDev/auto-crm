"""
Regression test suite for Dashboard TopPartsService under Period Transactional Accounting.

Accounting Invariants:
- sold_qty = SUM(SaleItem.quantity)
- sold_revenue = SUM(SaleItem.total_price)
- returned_qty = SUM(SaleReturnItem.quantity)
- returned_revenue = SUM(SaleReturnItem.total_price)
- net_qty = sold_qty - returned_qty
- net_revenue = sold_revenue - returned_revenue
- Filter: net_qty > 0
- Correlated scalar subqueries for returns grouped by product_id (Zero Cartesian multiplication)
- Mandatory cross-period return invariant: past periods remain unchanged; returns deduct in return period
- Soft-deleted sales (sale__deleted_at__isnull=True) and their returns are excluded
- Store isolation and consolidated mode (store_id='all' / None)
- DEBT sales included
- Respects total_price (actual discounted line amounts)
- TOP_PARTS_LIMIT enforced
- Exact response contract: [{"id": int, "name": str, "sold": Decimal, "rev": Decimal}]
"""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.products.models import Product
from apps.reports.services.dashboard_service import DateRange, TopPartsService
from apps.reports.views.dashboard_view import DashboardAPIView
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store
from apps.users.models.user import User


class DashboardTopPartsPeriodAccountingTest(TestCase):
    DT_START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    DT_END = datetime(2026, 2, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    DEFAULT_DT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=dt_timezone.utc)

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

        cls.prod_pad = Product.objects.create(
            name="Brake Pad",
            sku="DTP-001",
            barcode="555500000001",
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_disc = Product.objects.create(
            name="Brake Disc",
            sku="DTP-002",
            barcode="555500000002",
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_filter = Product.objects.create(
            name="Oil Filter",
            sku="DTP-003",
            barcode="555500000003",
            status=Product.ProductStatus.ACTIVE,
        )

        cls.factory = APIRequestFactory()

    def setUp(self):
        cache.clear()

    def _get_dr(self, start=None, end=None):
        return DateRange(
            current_from=start or self.DT_START,
            current_to=end or self.DT_END,
            prev_from=self.DT_START,
            prev_to=self.DT_END,
        )

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
        """1. Normal sale without returns: computes accurate sold quantity and revenue."""
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("100.00"))
        self._create_sale(self.store_a, self.prod_disc, qty=Decimal("5"), unit_price=Decimal("200.00"))

        dr = self._get_dr()
        res = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(len(res), 2)
        # Ordered by -sold: Brake Pad (10) > Brake Disc (5)
        self.assertEqual(res[0]["id"], self.prod_pad.id)
        self.assertEqual(res[0]["name"], "Brake Pad")
        self.assertEqual(res[0]["sold"], Decimal("10.00"))
        self.assertEqual(res[0]["rev"], Decimal("1000.00"))

        self.assertEqual(res[1]["id"], self.prod_disc.id)
        self.assertEqual(res[1]["name"], "Brake Disc")
        self.assertEqual(res[1]["sold"], Decimal("5.00"))
        self.assertEqual(res[1]["rev"], Decimal("1000.00"))

    def test_02_partial_return_same_period(self):
        """2. Partial return in the same period: net_qty = sold_qty - ret_qty, net_rev = sold_rev - ret_rev."""
        sale, item = self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("100.00")
        )
        self._create_return(
            sale, item, self.prod_pad, qty=Decimal("3"), refund_amount=Decimal("300.00")
        )

        dr = self._get_dr()
        res = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], self.prod_pad.id)
        self.assertEqual(res[0]["sold"], Decimal("7.00"))
        self.assertEqual(res[0]["rev"], Decimal("700.00"))

    def test_03_full_return_same_period(self):
        """3. Full return in the same period: net_qty <= 0 is excluded from results."""
        sale, item = self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("5"), unit_price=Decimal("100.00")
        )
        self._create_return(
            sale, item, self.prod_pad, qty=Decimal("5"), refund_amount=Decimal("500.00")
        )
        # prod_disc has 2 sold, no returns
        self._create_sale(self.store_a, self.prod_disc, qty=Decimal("2"), unit_price=Decimal("150.00"))

        dr = self._get_dr()
        res = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], self.prod_disc.id)
        self.assertEqual(res[0]["sold"], Decimal("2.00"))
        self.assertEqual(res[0]["rev"], Decimal("300.00"))

    def test_04_cross_period_return_past_period_unchanged(self):
        """4. Mandatory Cross-period test: September sale (10, 1M) returned in October does NOT modify September."""
        dt_sept = datetime(2026, 9, 15, 12, 0, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 5, 14, 0, 0, tzinfo=dt_timezone.utc)

        sale, item = self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("100000.00"), created_at=dt_sept
        )
        self._create_return(
            sale, item, self.prod_pad, qty=Decimal("4"), refund_amount=Decimal("400000.00"), created_at=dt_oct
        )

        dr_sept = self._get_dr(
            start=datetime(2026, 9, 1, 0, 0, 0, tzinfo=dt_timezone.utc),
            end=datetime(2026, 10, 1, 0, 0, 0, tzinfo=dt_timezone.utc),
        )
        res_sept = TopPartsService.get(store_id=str(self.store_a.id), dr=dr_sept)

        # September must still reflect full 10 qty and 1,000,000 revenue
        self.assertEqual(len(res_sept), 1)
        self.assertEqual(res_sept[0]["id"], self.prod_pad.id)
        self.assertEqual(res_sept[0]["sold"], Decimal("10.00"))
        self.assertEqual(res_sept[0]["rev"], Decimal("1000000.00"))

    def test_05_current_period_sale_plus_cross_period_return(self):
        """5. October has new sale (10 qty, 1.2M) and September return (4 qty, 400k) -> net 6 qty, 800k rev."""
        dt_sept = datetime(2026, 9, 15, 12, 0, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 5, 14, 0, 0, tzinfo=dt_timezone.utc)

        # September sale
        sale_sept, item_sept = self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("100000.00"), created_at=dt_sept
        )
        # October return from September sale (-4 qty, -400k)
        self._create_return(
            sale_sept, item_sept, self.prod_pad, qty=Decimal("4"), refund_amount=Decimal("400000.00"), created_at=dt_oct
        )
        # October new sale (+10 qty, +1.2M)
        self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("120000.00"), created_at=dt_oct
        )

        dr_oct = self._get_dr(
            start=datetime(2026, 10, 1, 0, 0, 0, tzinfo=dt_timezone.utc),
            end=datetime(2026, 11, 1, 0, 0, 0, tzinfo=dt_timezone.utc),
        )
        res_oct = TopPartsService.get(store_id=str(self.store_a.id), dr=dr_oct)

        # October net: 10 - 4 = 6 qty, 1,200,000 - 400,000 = 800,000 rev
        self.assertEqual(len(res_oct), 1)
        self.assertEqual(res_oct[0]["id"], self.prod_pad.id)
        self.assertEqual(res_oct[0]["sold"], Decimal("6.00"))
        self.assertEqual(res_oct[0]["rev"], Decimal("800000.00"))

    def test_06_return_only_product_in_current_period_excluded(self):
        """6. Return-only product in current period has net_qty <= 0 and MUST NOT appear in Top Parts."""
        dt_sept = datetime(2026, 9, 15, 12, 0, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 5, 14, 0, 0, tzinfo=dt_timezone.utc)

        # September sale for Brake Pad
        sale_sept, item_sept = self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("100.00"), created_at=dt_sept
        )
        # October return (-4 qty)
        self._create_return(
            sale_sept, item_sept, self.prod_pad, qty=Decimal("4"), refund_amount=Decimal("400.00"), created_at=dt_oct
        )
        # October sale only for Brake Disc (+5 qty)
        self._create_sale(
            self.store_a, self.prod_disc, qty=Decimal("5"), unit_price=Decimal("100.00"), created_at=dt_oct
        )

        dr_oct = self._get_dr(
            start=datetime(2026, 10, 1, 0, 0, 0, tzinfo=dt_timezone.utc),
            end=datetime(2026, 11, 1, 0, 0, 0, tzinfo=dt_timezone.utc),
        )
        res_oct = TopPartsService.get(store_id=str(self.store_a.id), dr=dr_oct)

        # Brake Pad has net_qty = -4 -> excluded. Only Brake Disc appears.
        self.assertEqual(len(res_oct), 1)
        self.assertEqual(res_oct[0]["id"], self.prod_disc.id)
        self.assertEqual(res_oct[0]["sold"], Decimal("5.00"))

    def test_07_deleted_sale_excluded(self):
        """7. Soft-deleted sale (deleted_at IS NOT NULL) is completely excluded from Top Parts."""
        dt = datetime(2026, 1, 10, 10, 0, 0, tzinfo=dt_timezone.utc)
        del_dt = datetime(2026, 1, 12, 10, 0, 0, tzinfo=dt_timezone.utc)

        # Deleted sale for Brake Pad
        self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("100.00"), created_at=dt, deleted_at=del_dt
        )
        # Active sale for Brake Disc
        self._create_sale(
            self.store_a, self.prod_disc, qty=Decimal("5"), unit_price=Decimal("100.00"), created_at=dt
        )

        dr = self._get_dr()
        res = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], self.prod_disc.id)

    def test_08_return_belonging_to_deleted_sale_excluded(self):
        """8. Return belonging to a soft-deleted sale is excluded and does not deduct active sales."""
        dt = datetime(2026, 1, 10, 10, 0, 0, tzinfo=dt_timezone.utc)
        del_dt = datetime(2026, 1, 12, 10, 0, 0, tzinfo=dt_timezone.utc)

        # Active sale for Brake Pad (+5 qty, 500 rev)
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("5"), unit_price=Decimal("100.00"), created_at=dt)

        # Deleted sale for Brake Pad (+5 qty) and its return (-5 qty)
        del_sale, del_item = self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("5"), unit_price=Decimal("100.00"), created_at=dt, deleted_at=del_dt
        )
        self._create_return(
            del_sale, del_item, self.prod_pad, qty=Decimal("5"), refund_amount=Decimal("500.00"), created_at=dt
        )

        dr = self._get_dr()
        res = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)

        # Return from deleted sale must not deduct from active sale
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], self.prod_pad.id)
        self.assertEqual(res[0]["sold"], Decimal("5.00"))
        self.assertEqual(res[0]["rev"], Decimal("500.00"))

    def test_09_store_isolation(self):
        """9. Store isolation: Store A and Store B do not bleed into each other."""
        # Store A: Brake Pad (10)
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("100.00"))
        # Store B: Brake Disc (20)
        self._create_sale(self.store_b, self.prod_disc, qty=Decimal("20"), unit_price=Decimal("100.00"))

        dr = self._get_dr()
        res_a = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)
        self.assertEqual(len(res_a), 1)
        self.assertEqual(res_a[0]["id"], self.prod_pad.id)
        self.assertEqual(res_a[0]["sold"], Decimal("10.00"))

        res_b = TopPartsService.get(store_id=str(self.store_b.id), dr=dr)
        self.assertEqual(len(res_b), 1)
        self.assertEqual(res_b[0]["id"], self.prod_disc.id)
        self.assertEqual(res_b[0]["sold"], Decimal("20.00"))

    def test_10_consolidated_stores(self):
        """10. Consolidated stores (store_id='all' or None) aggregates across all stores."""
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("6"), unit_price=Decimal("100.00"))
        self._create_sale(self.store_b, self.prod_pad, qty=Decimal("4"), unit_price=Decimal("100.00"))

        dr = self._get_dr()

        # store_id="all"
        res_all = TopPartsService.get(store_id="all", dr=dr)
        self.assertEqual(len(res_all), 1)
        self.assertEqual(res_all[0]["sold"], Decimal("10.00"))
        self.assertEqual(res_all[0]["rev"], Decimal("1000.00"))

        # store_id=None
        res_none = TopPartsService.get(store_id=None, dr=dr)
        self.assertEqual(len(res_none), 1)
        self.assertEqual(res_none[0]["sold"], Decimal("10.00"))

    def test_11_multiple_sales_and_multiple_returns_cartesian_prevention(self):
        """11. Multiple sales and multiple returns do not produce Cartesian multiplication."""
        sale1, item1 = self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("100.00")
        )
        sale2, item2 = self._create_sale(
            self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("100.00")
        )

        # Two distinct returns for item1
        self._create_return(sale1, item1, self.prod_pad, qty=Decimal("2"), refund_amount=Decimal("200.00"))
        self._create_return(sale1, item1, self.prod_pad, qty=Decimal("3"), refund_amount=Decimal("300.00"))
        # One return for item2
        self._create_return(sale2, item2, self.prod_pad, qty=Decimal("1"), refund_amount=Decimal("100.00"))

        dr = self._get_dr()
        res = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)

        # Total sold = 20, Total returned = 6 -> net sold = 14, net rev = 1400.
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], self.prod_pad.id)
        self.assertEqual(res[0]["sold"], Decimal("14.00"))
        self.assertEqual(res[0]["rev"], Decimal("1400.00"))

    def test_12_discount_and_total_price_correctness(self):
        """12. Uses actual total_price (respecting discounts) instead of quantity * unit_price."""
        # 10 units at unit_price 100, but discounted total_price is 800 (not 1000)
        sale, item = self._create_sale(
            self.store_a,
            self.prod_pad,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            total_price=Decimal("800.00"),
        )
        # Return 2 units with refund amount 160 (discounted return total_price)
        self._create_return(
            sale, item, self.prod_pad, qty=Decimal("2"), refund_amount=Decimal("160.00")
        )

        dr = self._get_dr()
        res = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["sold"], Decimal("8.00"))
        # net revenue: 800 - 160 = 640 (NOT (10 - 2) * 100 = 800)
        self.assertEqual(res[0]["rev"], Decimal("640.00"))

    def test_13_top_parts_limit(self):
        """13. TOP_PARTS_LIMIT: Returns at most 5 products ordered by net_qty DESC."""
        prods = [
            Product.objects.create(
                name=f"Extra Product {i}",
                sku=f"EXT-00{i}",
                barcode=f"55550000001{i}",
                status=Product.ProductStatus.ACTIVE,
            )
            for i in range(8)
        ]
        for idx, p in enumerate(prods):
            self._create_sale(self.store_a, p, qty=Decimal(str(idx + 1)), unit_price=Decimal("50.00"))

        dr = self._get_dr()
        res = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)

        # Must return exactly TOP_PARTS_LIMIT = 5 products
        self.assertEqual(len(res), 5)
        # Top 1 must have qty 8
        self.assertEqual(res[0]["name"], "Extra Product 7")
        self.assertEqual(res[0]["sold"], Decimal("8.00"))
        self.assertEqual(res[4]["name"], "Extra Product 3")
        self.assertEqual(res[4]["sold"], Decimal("4.00"))

    def test_14_response_contract(self):
        """14. Response contract: [{"id": int, "name": str, "sold": Decimal, "rev": Decimal}]"""
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("100.00"))

        dr = self._get_dr()
        res = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(len(res), 1)
        row = res[0]
        self.assertEqual(set(row.keys()), {"id", "name", "sold", "rev"})
        self.assertIsInstance(row["id"], int)
        self.assertIsInstance(row["name"], str)
        self.assertIsInstance(row["sold"], Decimal)
        self.assertIsInstance(row["rev"], Decimal)

    def test_15_date_boundary_behavior(self):
        """15. Date boundary behavior: sale__created_at >= dr.current_from and sale__created_at < dr.current_to."""
        dt_before = datetime(2025, 12, 31, 23, 59, 59, tzinfo=dt_timezone.utc)
        dt_start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
        dt_mid = datetime(2026, 1, 15, 12, 0, 0, tzinfo=dt_timezone.utc)
        dt_end = datetime(2026, 2, 1, 0, 0, 0, tzinfo=dt_timezone.utc)

        # Before start (excluded)
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10"), created_at=dt_before)
        # Exactly at start (included)
        self._create_sale(self.store_a, self.prod_disc, qty=Decimal("5"), created_at=dt_start)
        # Inside (included)
        self._create_sale(self.store_a, self.prod_filter, qty=Decimal("7"), created_at=dt_mid)
        # Exactly at end (excluded with < dt_end)
        prod_outside = Product.objects.create(name="Outside", sku="OUT-001", barcode="555500000099")
        self._create_sale(self.store_a, prod_outside, qty=Decimal("20"), created_at=dt_end)

        dr = self._get_dr(start=dt_start, end=dt_end)
        res = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)

        prod_ids = [r["id"] for r in res]
        self.assertNotIn(self.prod_pad.id, prod_ids)
        self.assertIn(self.prod_disc.id, prod_ids)
        self.assertIn(self.prod_filter.id, prod_ids)
        self.assertNotIn(prod_outside.id, prod_ids)

    def test_16_debt_sale_included(self):
        """16. Sale with status=DEBT is included in Top Parts."""
        self._create_sale(
            self.store_a,
            self.prod_pad,
            qty=Decimal("12"),
            unit_price=Decimal("100.00"),
            status=Sale.Status.DEBT,
            payment_type="debt",
        )

        dr = self._get_dr()
        res = TopPartsService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], self.prod_pad.id)
        self.assertEqual(res[0]["sold"], Decimal("12.00"))
        self.assertEqual(res[0]["rev"], Decimal("1200.00"))

    def test_17_dashboard_api_view_integration(self):
        """17. Integration: GET /api/v1/reports/dashboard/ returns topParts matching Period Transactional semantics."""
        self._create_sale(self.store_a, self.prod_pad, qty=Decimal("10"), unit_price=Decimal("100.00"))

        view = DashboardAPIView.as_view()
        req = self.factory.get(f"/api/v1/reports/dashboard/?store_id={self.store_a.id}&from=2026-01-01&to=2026-01-31")
        force_authenticate(req, user=self.admin_user)

        response = view(req)
        self.assertEqual(response.status_code, 200)
        self.assertIn("topParts", response.data)

        top_parts = response.data["topParts"]
        self.assertEqual(len(top_parts), 1)
        self.assertEqual(top_parts[0]["id"], self.prod_pad.id)
        self.assertEqual(top_parts[0]["name"], "Brake Pad")
        self.assertEqual(top_parts[0]["sold"], Decimal("10.00"))
        self.assertEqual(top_parts[0]["rev"], Decimal("1000.00"))
