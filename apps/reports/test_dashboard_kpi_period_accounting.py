"""
Regression test suite for Dashboard KPIService under Period Transactional Accounting.

Accounting Invariants:
- SALE STREAM:
    Sale.created_at in [start, end) and Sale.deleted_at IS NULL
    sold_revenue = SUM(Sale.total_amount)
    sold_paid = SUM(Sale.paid_amount)
    sold_debt = SUM(GREATEST(0, Sale.total_amount - Sale.paid_amount))
    orders = COUNT(Sale.id) (All sales created in period, NOT excluded even if returned)

- RETURN STREAM:
    SaleReturn.created_at in [start, end) and SaleReturn.sale.deleted_at IS NULL
    return_revenue = SUM(SaleReturn.total_refund)
    debt_reduced = SUM(LEAST(GREATEST(0, sale.total_amount - sale.paid_amount), total_refund))
    paid_refunded = return_revenue - debt_reduced (or from Payment.is_refund=True if present)

- NET PERIOD METRICS:
    revenue = sold_revenue - return_revenue
    paid = sold_paid - paid_refunded
    debt = sold_debt - debt_reduced
    Fundamental identity holds in ALL periods: revenue == paid + debt

- MANDATORY CROSS-PERIOD INVARIANT:
    Sep 30 Sale (1,000,000) -> Sep revenue = +1,000,000, Sep orders = 1
    Oct 01 Return (1,000,000) -> Oct revenue = -1,000,000, Oct orders = 0
    Original September period is NEVER retroactively modified!

- Soft-deleted sales and their returns/payments are excluded.
- Store isolation and consolidated ('all' / None) mode.
- Exact response contract preserved:
    revenue, revenueGrowth, paid, debt, debtGrowth, orders, ordersGrowth, lowStockCount
"""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.products.models import Product, ProductBatch
from apps.reports.services.dashboard_service import DateRange, KPIService
from apps.reports.views.dashboard_view import DashboardAPIView
from apps.sales.models import Payment, Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store
from apps.users.models.user import User


class DashboardKPIPeriodAccountingTest(TestCase):
    SEP_START = datetime(2026, 9, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    SEP_END = datetime(2026, 10, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    OCT_START = datetime(2026, 10, 1, 0, 0, 0, tzinfo=dt_timezone.utc)
    OCT_END = datetime(2026, 11, 1, 0, 0, 0, tzinfo=dt_timezone.utc)

    DT_SEP_15 = datetime(2026, 9, 15, 12, 0, 0, tzinfo=dt_timezone.utc)
    DT_SEP_20 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=dt_timezone.utc)
    DT_SEP_30 = datetime(2026, 9, 30, 23, 0, 0, tzinfo=dt_timezone.utc)
    DT_OCT_01 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=dt_timezone.utc)
    DT_OCT_15 = datetime(2026, 10, 15, 12, 0, 0, tzinfo=dt_timezone.utc)

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

        cls.prod_a = Product.objects.create(
            name="Product A",
            sku="SKU-001",
            barcode="555500000001",
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_b = Product.objects.create(
            name="Product B",
            sku="SKU-002",
            barcode="555500000002",
            status=Product.ProductStatus.ACTIVE,
        )

        # Batches for low stock test
        cls.batch_low = ProductBatch.objects.create(
            store=cls.store_a,
            product=cls.prod_a,
            quantity=Decimal("3.00"),  # < LOW_STOCK_THRESHOLD (5)
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            is_active=True,
        )
        cls.batch_ok = ProductBatch.objects.create(
            store=cls.store_a,
            product=cls.prod_b,
            quantity=Decimal("10.00"),  # >= LOW_STOCK_THRESHOLD (5)
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            is_active=True,
        )

        cls.factory = APIRequestFactory()

    def setUp(self):
        cache.clear()

    def _get_dr(self, cur_from, cur_to, prev_from=None, prev_to=None):
        return DateRange(
            current_from=cur_from,
            current_to=cur_to,
            prev_from=prev_from or cur_from,
            prev_to=prev_to or cur_to,
        )

    def _create_sale(
        self,
        store,
        total_amount,
        paid_amount=None,
        status=Sale.Status.PAID,
        payment_type="cash",
        created_at=None,
        deleted_at=None,
    ):
        if paid_amount is None:
            paid_amount = total_amount if status == Sale.Status.PAID else Decimal("0.00")

        sale = Sale.objects.create(
            store=store,
            seller=self.admin_user,
            total_amount=total_amount,
            paid_amount=paid_amount,
            status=status,
            payment_type=payment_type,
        )
        item = SaleItem.objects.create(
            sale=sale,
            product=self.prod_a,
            quantity=Decimal("1.00"),
            unit_price=total_amount,
            total_price=total_amount,
        )
        dt = created_at or self.DT_SEP_15
        Sale.objects.filter(id=sale.id).update(created_at=dt)
        if deleted_at:
            Sale.all_objects.filter(id=sale.id).update(deleted_at=deleted_at)
        sale.refresh_from_db()
        item.refresh_from_db()
        return sale, item

    def _create_return(
        self,
        sale,
        refund_amount,
        created_at=None,
        store=None,
    ):
        item = sale.items.first()
        sale_return = SaleReturn.objects.create(
            sale=sale,
            store=store or sale.store,
            seller=self.admin_user,
            total_refund=refund_amount,
        )
        ret_item = SaleReturnItem.objects.create(
            sale_return=sale_return,
            sale_item=item,
            product=item.product,
            quantity=Decimal("1.00"),
            unit_price=refund_amount,
            total_price=refund_amount,
        )
        dt = created_at or self.DT_SEP_20
        SaleReturn.objects.filter(id=sale_return.id).update(created_at=dt)
        sale_return.refresh_from_db()
        ret_item.refresh_from_db()
        return sale_return, ret_item

    # ─────────────────────────────────────────────────────────────
    # 1. Normal sale inside period
    # ─────────────────────────────────────────────────────────────
    def test_01_normal_sale_inside_period(self):
        """1. Normal sale inside period: revenue, paid, debt and orders match exactly."""
        self._create_sale(
            self.store_a,
            total_amount=Decimal("1000000.00"),
            paid_amount=Decimal("1000000.00"),
            status=Sale.Status.PAID,
            created_at=self.DT_SEP_15,
        )

        dr = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)
        res = KPIService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(res["revenue"], Decimal("1000000.00"))
        self.assertEqual(res["paid"], Decimal("1000000.00"))
        self.assertEqual(res["debt"], Decimal("0.00"))
        self.assertEqual(res["orders"], 1)
        self.assertEqual(res["revenue"], res["paid"] + res["debt"])

    # ─────────────────────────────────────────────────────────────
    # 2. Partial return inside same period
    # ─────────────────────────────────────────────────────────────
    def test_02_partial_return_inside_same_period(self):
        """2. Partial return in same period: revenue drops by refund, orders remains 1."""
        sale, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("1000000.00"),
            paid_amount=Decimal("1000000.00"),
            status=Sale.Status.PAID,
            created_at=self.DT_SEP_15,
        )
        self._create_return(
            sale,
            refund_amount=Decimal("300000.00"),
            created_at=self.DT_SEP_20,
        )

        dr = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)
        res = KPIService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(res["revenue"], Decimal("700000.00"))
        self.assertEqual(res["paid"], Decimal("700000.00"))
        self.assertEqual(res["debt"], Decimal("0.00"))
        self.assertEqual(res["orders"], 1)
        self.assertEqual(res["revenue"], res["paid"] + res["debt"])

    # ─────────────────────────────────────────────────────────────
    # 3. Full return inside same period
    # ─────────────────────────────────────────────────────────────
    def test_03_full_return_inside_same_period(self):
        """3. Full return in same period: net revenue becomes 0, order remains counted (1)."""
        sale, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("1000000.00"),
            paid_amount=Decimal("1000000.00"),
            status=Sale.Status.PAID,
            created_at=self.DT_SEP_15,
        )
        self._create_return(
            sale,
            refund_amount=Decimal("1000000.00"),
            created_at=self.DT_SEP_20,
        )
        Sale.objects.filter(id=sale.id).update(status=Sale.Status.RETURNED)

        dr = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)
        res = KPIService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(res["revenue"], Decimal("0.00"))
        self.assertEqual(res["paid"], Decimal("0.00"))
        self.assertEqual(res["debt"], Decimal("0.00"))
        self.assertEqual(res["orders"], 1)  # Period Transactional: sale was created in this period
        self.assertEqual(res["revenue"], res["paid"] + res["debt"])

    # ─────────────────────────────────────────────────────────────
    # 4. Critical cross-period full return (Sep 30 sale, Oct 1 return)
    # ─────────────────────────────────────────────────────────────
    def test_04_cross_period_full_return(self):
        """4. Critical cross-period full return: Sep remains +1M & 1 order; Oct is -1M & 0 orders."""
        sale, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("1000000.00"),
            paid_amount=Decimal("1000000.00"),
            status=Sale.Status.PAID,
            created_at=self.DT_SEP_30,
        )
        self._create_return(
            sale,
            refund_amount=Decimal("1000000.00"),
            created_at=self.DT_OCT_01,
        )
        Sale.objects.filter(id=sale.id).update(status=Sale.Status.RETURNED)

        # September check
        dr_sep = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)
        res_sep = KPIService.get(store_id=str(self.store_a.id), dr=dr_sep)

        self.assertEqual(res_sep["revenue"], Decimal("1000000.00"))
        self.assertEqual(res_sep["paid"], Decimal("1000000.00"))
        self.assertEqual(res_sep["debt"], Decimal("0.00"))
        self.assertEqual(res_sep["orders"], 1)
        self.assertEqual(res_sep["revenue"], res_sep["paid"] + res_sep["debt"])

        # October check
        dr_oct = self._get_dr(
            cur_from=self.OCT_START,
            cur_to=self.OCT_END,
            prev_from=self.SEP_START,
            prev_to=self.SEP_END,
        )
        res_oct = KPIService.get(store_id=str(self.store_a.id), dr=dr_oct)

        self.assertEqual(res_oct["revenue"], Decimal("-1000000.00"))
        self.assertEqual(res_oct["paid"], Decimal("-1000000.00"))
        self.assertEqual(res_oct["debt"], Decimal("0.00"))
        self.assertEqual(res_oct["orders"], 0)
        self.assertEqual(res_oct["revenue"], res_oct["paid"] + res_oct["debt"])

        # Growth comparisons between Oct and Sep
        self.assertEqual(res_oct["revenueGrowth"], -200.0)  # (-1M - 1M) / 1M * 100
        self.assertEqual(res_oct["ordersGrowth"], -100.0)   # (0 - 1) / 1 * 100

    # ─────────────────────────────────────────────────────────────
    # 5. Critical cross-period partial return (Sep 30 sale, Oct 1 return)
    # ─────────────────────────────────────────────────────────────
    def test_05_cross_period_partial_return(self):
        """5. Cross-period partial return: Sep remains +1,000,000 (not 700k); Oct is -300,000."""
        sale, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("1000000.00"),
            paid_amount=Decimal("1000000.00"),
            status=Sale.Status.PAID,
            created_at=self.DT_SEP_30,
        )
        self._create_return(
            sale,
            refund_amount=Decimal("300000.00"),
            created_at=self.DT_OCT_01,
        )

        # September check
        dr_sep = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)
        res_sep = KPIService.get(store_id=str(self.store_a.id), dr=dr_sep)
        self.assertEqual(res_sep["revenue"], Decimal("1000000.00"))
        self.assertEqual(res_sep["paid"], Decimal("1000000.00"))
        self.assertEqual(res_sep["debt"], Decimal("0.00"))
        self.assertEqual(res_sep["orders"], 1)

        # October check
        dr_oct = self._get_dr(cur_from=self.OCT_START, cur_to=self.OCT_END)
        res_oct = KPIService.get(store_id=str(self.store_a.id), dr=dr_oct)
        self.assertEqual(res_oct["revenue"], Decimal("-300000.00"))
        self.assertEqual(res_oct["paid"], Decimal("-300000.00"))
        self.assertEqual(res_oct["debt"], Decimal("0.00"))
        self.assertEqual(res_oct["orders"], 0)
        self.assertEqual(res_oct["revenue"], res_oct["paid"] + res_oct["debt"])

    # ─────────────────────────────────────────────────────────────
    # 6. Return-only period
    # ─────────────────────────────────────────────────────────────
    def test_06_return_only_period(self):
        """6. Period with only returns (no sales): negative revenue, 0 orders."""
        sale, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("500000.00"),
            paid_amount=Decimal("500000.00"),
            status=Sale.Status.PAID,
            created_at=self.DT_SEP_15,
        )
        self._create_return(
            sale,
            refund_amount=Decimal("500000.00"),
            created_at=self.DT_OCT_15,
        )

        dr_oct = self._get_dr(cur_from=self.OCT_START, cur_to=self.OCT_END)
        res_oct = KPIService.get(store_id=str(self.store_a.id), dr=dr_oct)

        self.assertEqual(res_oct["revenue"], Decimal("-500000.00"))
        self.assertEqual(res_oct["paid"], Decimal("-500000.00"))
        self.assertEqual(res_oct["debt"], Decimal("0.00"))
        self.assertEqual(res_oct["orders"], 0)

    # ─────────────────────────────────────────────────────────────
    # 7. Return-only store
    # ─────────────────────────────────────────────────────────────
    def test_07_return_only_store(self):
        """7. Store A has only a return in period; Store B has sales."""
        # Sale originally happened in August for Store A
        dt_aug = datetime(2026, 8, 20, 12, 0, 0, tzinfo=dt_timezone.utc)
        sale_a, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("200000.00"),
            paid_amount=Decimal("200000.00"),
            created_at=dt_aug,
        )
        # In September: Store A has return of 200k, Store B has sale of 600k
        self._create_return(
            sale_a,
            refund_amount=Decimal("200000.00"),
            created_at=self.DT_SEP_15,
        )
        self._create_sale(
            self.store_b,
            total_amount=Decimal("600000.00"),
            paid_amount=Decimal("600000.00"),
            created_at=self.DT_SEP_20,
        )

        dr = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)

        # Store A: return-only
        res_a = KPIService.get(store_id=str(self.store_a.id), dr=dr)
        self.assertEqual(res_a["revenue"], Decimal("-200000.00"))
        self.assertEqual(res_a["paid"], Decimal("-200000.00"))
        self.assertEqual(res_a["debt"], Decimal("0.00"))
        self.assertEqual(res_a["orders"], 0)

        # Store B: sale-only
        res_b = KPIService.get(store_id=str(self.store_b.id), dr=dr)
        self.assertEqual(res_b["revenue"], Decimal("600000.00"))
        self.assertEqual(res_b["orders"], 1)

        # Consolidated 'all': net is 400k, orders is 1
        res_all = KPIService.get(store_id="all", dr=dr)
        self.assertEqual(res_all["revenue"], Decimal("400000.00"))
        self.assertEqual(res_all["orders"], 1)

    # ─────────────────────────────────────────────────────────────
    # 8. Soft-deleted sale
    # ─────────────────────────────────────────────────────────────
    def test_08_soft_deleted_sale(self):
        """8. Soft-deleted sale and its return are excluded from all KPI streams."""
        sale, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("400000.00"),
            paid_amount=Decimal("400000.00"),
            created_at=self.DT_SEP_15,
            deleted_at=self.DT_SEP_20,
        )
        self._create_return(
            sale,
            refund_amount=Decimal("150000.00"),
            created_at=self.DT_SEP_20,
        )

        dr = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)
        res = KPIService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(res["revenue"], Decimal("0.00"))
        self.assertEqual(res["paid"], Decimal("0.00"))
        self.assertEqual(res["debt"], Decimal("0.00"))
        self.assertEqual(res["orders"], 0)

    # ─────────────────────────────────────────────────────────────
    # 9. Multiple returns across multiple sales
    # ─────────────────────────────────────────────────────────────
    def test_09_multiple_returns(self):
        """9. Multiple returns across multiple sales inside the period."""
        sale1, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("400000.00"),
            paid_amount=Decimal("400000.00"),
            created_at=self.DT_SEP_15,
        )
        self._create_return(sale1, refund_amount=Decimal("100000.00"), created_at=self.DT_SEP_20)

        sale2, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("600000.00"),
            paid_amount=Decimal("600000.00"),
            created_at=self.DT_SEP_15,
        )
        self._create_return(sale2, refund_amount=Decimal("150000.00"), created_at=self.DT_SEP_20)
        self._create_return(sale2, refund_amount=Decimal("50000.00"), created_at=self.DT_SEP_30)

        dr = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)
        res = KPIService.get(store_id=str(self.store_a.id), dr=dr)

        # Total sales: 1,000,000; Total returns: 300,000 -> Net: 700,000; Orders: 2
        self.assertEqual(res["revenue"], Decimal("700000.00"))
        self.assertEqual(res["paid"], Decimal("700000.00"))
        self.assertEqual(res["debt"], Decimal("0.00"))
        self.assertEqual(res["orders"], 2)

    # ─────────────────────────────────────────────────────────────
    # 10. Multiple stores and store filtering
    # ─────────────────────────────────────────────────────────────
    def test_10_multiple_stores_and_filtering(self):
        """10. Filter by store_a, store_b, and 'all'."""
        sale_a, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("500000.00"),
            paid_amount=Decimal("500000.00"),
            created_at=self.DT_SEP_15,
        )
        self._create_return(sale_a, refund_amount=Decimal("100000.00"), created_at=self.DT_SEP_20)

        sale_b, _ = self._create_sale(
            self.store_b,
            total_amount=Decimal("800000.00"),
            paid_amount=Decimal("800000.00"),
            created_at=self.DT_SEP_15,
        )
        self._create_return(sale_b, refund_amount=Decimal("200000.00"), created_at=self.DT_SEP_20)

        dr = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)

        # Store A: net 400k, orders 1
        res_a = KPIService.get(store_id=str(self.store_a.id), dr=dr)
        self.assertEqual(res_a["revenue"], Decimal("400000.00"))
        self.assertEqual(res_a["orders"], 1)

        # Store B: net 600k, orders 1
        res_b = KPIService.get(store_id=str(self.store_b.id), dr=dr)
        self.assertEqual(res_b["revenue"], Decimal("600000.00"))
        self.assertEqual(res_b["orders"], 1)

        # All: net 1,000,000, orders 2
        res_all = KPIService.get(store_id="all", dr=dr)
        self.assertEqual(res_all["revenue"], Decimal("1000000.00"))
        self.assertEqual(res_all["orders"], 2)

    # ─────────────────────────────────────────────────────────────
    # 11. Empty period
    # ─────────────────────────────────────────────────────────────
    def test_11_empty_period(self):
        """11. Empty period returns zeros for amounts and growth, non-negative lowStockCount."""
        dr = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)
        res = KPIService.get(store_id=str(self.store_a.id), dr=dr)

        self.assertEqual(res["revenue"], Decimal("0.00"))
        self.assertEqual(res["paid"], Decimal("0.00"))
        self.assertEqual(res["debt"], Decimal("0.00"))
        self.assertEqual(res["orders"], 0)
        self.assertEqual(res["revenueGrowth"], 0.0)
        self.assertEqual(res["debtGrowth"], 0.0)
        self.assertEqual(res["ordersGrowth"], 0.0)
        self.assertGreaterEqual(res["lowStockCount"], 0)

    # ─────────────────────────────────────────────────────────────
    # 12. Existing KPI response contract
    # ─────────────────────────────────────────────────────────────
    def test_12_existing_kpi_response_contract(self):
        """12. Exact 8 keys with proper data types match the legacy API response contract."""
        self._create_sale(
            self.store_a,
            total_amount=Decimal("1000000.00"),
            paid_amount=Decimal("1000000.00"),
            created_at=self.DT_SEP_15,
        )

        dr = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)
        res = KPIService.get(store_id=str(self.store_a.id), dr=dr)

        expected_keys = {
            "revenue",
            "revenueGrowth",
            "paid",
            "debt",
            "debtGrowth",
            "orders",
            "ordersGrowth",
            "lowStockCount",
        }
        self.assertEqual(set(res.keys()), expected_keys)
        self.assertIsInstance(res["revenue"], Decimal)
        self.assertIsInstance(res["revenueGrowth"], float)
        self.assertIsInstance(res["paid"], Decimal)
        self.assertIsInstance(res["debt"], Decimal)
        self.assertIsInstance(res["debtGrowth"], float)
        self.assertIsInstance(res["orders"], int)
        self.assertIsInstance(res["ordersGrowth"], float)
        self.assertIsInstance(res["lowStockCount"], int)
        self.assertEqual(res["lowStockCount"], 1)  # Only batch_low (< 5)

    # ─────────────────────────────────────────────────────────────
    # 13. Debt sale and debt return accounting
    # ─────────────────────────────────────────────────────────────
    def test_13_debt_sale_and_debt_return_accounting(self):
        """13. Debt sale and subsequent return correctly reduce debt, preserving revenue == paid + debt."""
        # Sale with status=DEBT (total 1M, paid 0, debt 1M)
        sale, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("1000000.00"),
            paid_amount=Decimal("0.00"),
            status=Sale.Status.DEBT,
            payment_type="debt",
            created_at=self.DT_SEP_15,
        )

        # Sep check
        dr_sep = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)
        res_sep = KPIService.get(store_id=str(self.store_a.id), dr=dr_sep)
        self.assertEqual(res_sep["revenue"], Decimal("1000000.00"))
        self.assertEqual(res_sep["paid"], Decimal("0.00"))
        self.assertEqual(res_sep["debt"], Decimal("1000000.00"))
        self.assertEqual(res_sep["orders"], 1)
        self.assertEqual(res_sep["revenue"], res_sep["paid"] + res_sep["debt"])

        # Return in October of 400,000 (reduces debt, not cash)
        self._create_return(
            sale,
            refund_amount=Decimal("400000.00"),
            created_at=self.DT_OCT_01,
        )

        dr_oct = self._get_dr(cur_from=self.OCT_START, cur_to=self.OCT_END)
        res_oct = KPIService.get(store_id=str(self.store_a.id), dr=dr_oct)
        self.assertEqual(res_oct["revenue"], Decimal("-400000.00"))
        self.assertEqual(res_oct["paid"], Decimal("0.00"))
        self.assertEqual(res_oct["debt"], Decimal("-400000.00"))
        self.assertEqual(res_oct["orders"], 0)
        self.assertEqual(res_oct["revenue"], res_oct["paid"] + res_oct["debt"])

    # ─────────────────────────────────────────────────────────────
    # 14. Cash refund via Payment.is_refund=True
    # ─────────────────────────────────────────────────────────────
    def test_14_cash_refund_with_payment_record(self):
        """14. When explicit Payment.is_refund=True exists, paid_refunded is taken from payments."""
        sale, _ = self._create_sale(
            self.store_a,
            total_amount=Decimal("1000000.00"),
            paid_amount=Decimal("400000.00"),
            status=Sale.Status.PARTIAL,
            payment_type="mixed",
            created_at=self.DT_SEP_15,
        )
        ret, _ = self._create_return(
            sale,
            refund_amount=Decimal("500000.00"),
            created_at=self.DT_SEP_20,
        )
        # Suppose 200,000 was refunded in cash, and 300,000 reduced debt
        Payment.objects.create(
            sale=sale,
            customer=None,
            amount=Decimal("200000.00"),
            type=Payment.Type.CASH,
            is_refund=True,
        )
        Payment.objects.filter(sale=sale, is_refund=True).update(created_at=self.DT_SEP_20)

        dr = self._get_dr(cur_from=self.SEP_START, cur_to=self.SEP_END)
        res = KPIService.get(store_id=str(self.store_a.id), dr=dr)

        # Revenue: 1,000,000 - 500,000 = 500,000
        # Paid: 400,000 - 200,000 = 200,000
        # Debt: 600,000 - 300,000 = 300,000
        self.assertEqual(res["revenue"], Decimal("500000.00"))
        self.assertEqual(res["paid"], Decimal("200000.00"))
        self.assertEqual(res["debt"], Decimal("300000.00"))
        self.assertEqual(res["revenue"], res["paid"] + res["debt"])

    # ─────────────────────────────────────────────────────────────
    # 15. DashboardAPIView integration
    # ─────────────────────────────────────────────────────────────
    def test_15_dashboard_api_view_integration(self):
        """15. GET /api/v1/reports/dashboard/ returns kpi matching Period Transactional semantics."""
        self._create_sale(
            self.store_a,
            total_amount=Decimal("1000000.00"),
            paid_amount=Decimal("1000000.00"),
            created_at=datetime(2026, 9, 15, 12, 0, 0, tzinfo=dt_timezone.utc),
        )

        view = DashboardAPIView.as_view()
        req = self.factory.get(f"/api/v1/reports/dashboard/?store_id={self.store_a.id}&from=2026-09-01&to=2026-09-30")
        force_authenticate(req, user=self.admin_user)

        response = view(req)
        self.assertEqual(response.status_code, 200)
        self.assertIn("kpi", response.data)

        kpi = response.data["kpi"]
        self.assertEqual(kpi["revenue"], Decimal("1000000.00"))
        self.assertEqual(kpi["paid"], Decimal("1000000.00"))
        self.assertEqual(kpi["debt"], Decimal("0.00"))
        self.assertEqual(kpi["orders"], 1)
