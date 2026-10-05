"""
Regression test suite for BranchService under Period Transactional Accounting.

Accounting Invariants:
1. Sale stream: Sale.created_at ∈ [start, end), Sale.deleted_at IS NULL, store_id filter.
   - sold_revenue = SUM(Sale.total_amount)
   - orders = Count(Sale.id) (Status.RETURNED is NOT excluded)
   - customers = Count(distinct Sale.customer)

2. Return stream: SaleReturn.created_at ∈ [start, end), SaleReturn.sale.deleted_at IS NULL, store_id filter.
   - return_revenue = SUM(SaleReturn.total_refund) (or fallback to SaleReturnItem.total_price)

3. Zero Cartesian Merge:
   - Group sales by store_id, store__name
   - Group returns by store_id, store__name
   - Merge in Python: revenue = sold_revenue - return_revenue
   - Return-only store (sold = 0, return > 0) appears with revenue = -return_revenue, orders = 0, customers = 0
   - Ordered by -revenue

4. SummaryService consistency invariant:
   - SUM(branch.revenue) == SummaryService.get(...)["totalRevenue"]

5. Cross-period invariant:
   - Store A sale in Sept 2026: +1,000,000 rev, 1 order, 1 customer.
   - Store A return in Oct 2026: -1,000,000 rev.
   - Sept report remains 100% unchanged.
   - Oct report reflects -1,000,000 rev, 0 orders, 0 customers.
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.products.models import Category, Product
from apps.reports.services.excel_export_service import ExcelExportService
from apps.reports.services.report_service import BranchService, ReportService, SummaryService
from apps.reports.views.report_view import ReportsAPIView
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store
from apps.users.models.customers import Customer
from apps.users.models.user import User


class BranchStatisticsPeriodAccountingTest(TestCase):
    SEPT_DT = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
    OCT_DT = datetime(2026, 10, 5, 12, 0, tzinfo=dt_timezone.utc)

    @classmethod
    def setUpTestData(cls):
        cls.store_a = Store.objects.create(name="Store A", phone_number="+998901111111")
        cls.store_b = Store.objects.create(name="Store B", phone_number="+998902222222")
        cls.store_c = Store.objects.create(name="Store C", phone_number="+998903333333")

        cls.admin_user = User.objects.create(
            phone_number="+998904444444",
            email="admin@example.com",
            is_staff=True,
            is_superuser=True,
        )

        cls.cat_parts = Category.objects.create(name="Ehtiyot qismlar")

        cls.prod_oil = Product.objects.create(
            name="Motor Oil 5W-40",
            sku="OIL-001",
            barcode="777700000001",
            category=cls.cat_parts,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_filter = Product.objects.create(
            name="Oil Filter",
            sku="FLT-001",
            barcode="777700000002",
            category=cls.cat_parts,
            status=Product.ProductStatus.ACTIVE,
        )

        cls.cust_ali = Customer.objects.create(full_name="Ali Valiyev", phone_number="+998901112233")
        cls.cust_vali = Customer.objects.create(full_name="Vali Aliyev", phone_number="+998902223344")

        cls.factory = APIRequestFactory()

    def setUp(self):
        cache.clear()

    def _create_sale(
        self,
        store,
        product,
        qty=Decimal("1.00"),
        unit_price=Decimal("100.00"),
        purchase_price=Decimal("70.00"),
        customer=None,
        status=Sale.Status.PAID,
        created_at=None,
        deleted_at=None,
        discount_amount=Decimal("0.00"),
    ):
        total_items_amount = qty * unit_price
        total_amount = total_items_amount - discount_amount

        sale = Sale.objects.create(
            store=store,
            seller=self.admin_user,
            customer=customer,
            total_amount=total_amount,
            paid_amount=total_amount if status == Sale.Status.PAID else Decimal("0.00"),
            discount_amount=discount_amount,
            status=status,
            payment_type="cash",
        )
        item = SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=qty,
            unit_price=unit_price,
            purchase_price=purchase_price,
            total_price=total_items_amount,
        )
        dt = created_at or self.SEPT_DT
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
        qty=Decimal("1.00"),
        unit_price=None,
        refund_amount=None,
        created_at=None,
        store=None,
    ):
        if unit_price is None:
            unit_price = item.unit_price
        if refund_amount is None:
            refund_amount = qty * unit_price

        ret_store = store or sale.store
        sale_return = SaleReturn.objects.create(
            sale=sale,
            store=ret_store,
            seller=self.admin_user,
            customer=sale.customer,
            total_refund=refund_amount,
        )
        ret_item = SaleReturnItem.objects.create(
            sale_return=sale_return,
            sale_item=item,
            product=item.product,
            quantity=qty,
            unit_price=unit_price,
            total_price=refund_amount,
        )
        dt = created_at or self.OCT_DT
        SaleReturn.objects.filter(id=sale_return.id).update(created_at=dt)
        sale_return.refresh_from_db()
        ret_item.refresh_from_db()
        return sale_return, ret_item

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Normal sale
    # ─────────────────────────────────────────────────────────────────────────
    def test_01_normal_sale(self):
        """Single store sale in period."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            customer=self.cust_ali,
            created_at=self.SEPT_DT,
        )

        res = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["store_id"], self.store_a.id)
        self.assertEqual(res[0]["store__name"], self.store_a.name)
        self.assertEqual(res[0]["revenue"], Decimal("1000.00"))
        self.assertEqual(res[0]["orders"], 1)
        self.assertEqual(res[0]["customers"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Multiple stores
    # ─────────────────────────────────────────────────────────────────────────
    def test_02_multiple_stores_ranking(self):
        """Multiple stores ranked by revenue descending."""
        # Store A: 500
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100.00"),
            created_at=self.SEPT_DT,
        )
        # Store B: 1500
        self._create_sale(
            store=self.store_b,
            product=self.prod_oil,
            qty=Decimal("15"),
            unit_price=Decimal("100.00"),
            created_at=self.SEPT_DT,
        )
        # Store C: 800
        self._create_sale(
            store=self.store_c,
            product=self.prod_oil,
            qty=Decimal("8"),
            unit_price=Decimal("100.00"),
            created_at=self.SEPT_DT,
        )

        res = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), None)

        self.assertEqual(len(res), 3)
        self.assertEqual(res[0]["store_id"], self.store_b.id)
        self.assertEqual(res[0]["revenue"], Decimal("1500.00"))
        self.assertEqual(res[1]["store_id"], self.store_c.id)
        self.assertEqual(res[1]["revenue"], Decimal("800.00"))
        self.assertEqual(res[2]["store_id"], self.store_a.id)
        self.assertEqual(res[2]["revenue"], Decimal("500.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Same-period return
    # ─────────────────────────────────────────────────────────────────────────
    def test_03_same_period_return(self):
        """Sale and return both in September: return is subtracted from branch revenue."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            customer=self.cust_ali,
            created_at=datetime(2026, 9, 5, 10, 0, tzinfo=dt_timezone.utc),
        )
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("3"),
            refund_amount=Decimal("300.00"),
            created_at=datetime(2026, 9, 20, 15, 0, tzinfo=dt_timezone.utc),
        )

        res = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["revenue"], Decimal("700.00"))  # 1000 - 300
        self.assertEqual(res[0]["orders"], 1)
        self.assertEqual(res[0]["customers"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Cross-period return (Core Invariant)
    # ─────────────────────────────────────────────────────────────────────────
    def test_04_cross_period_return_invariant(self):
        """
        Cross-period invariant:
        September: Store A sale = +1,000,000, orders = 1, customers = 1
        October: Store A return = -1,000,000

        September BranchService: revenue = +1,000,000, orders = 1, customers = 1.
        October BranchService: revenue = -1,000,000, orders = 0, customers = 0.
        September result MUST NOT change after October return is recorded.
        """
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("1000"),
            unit_price=Decimal("1000.00"),
            customer=self.cust_ali,
            created_at=datetime(2026, 9, 30, 17, 0, tzinfo=dt_timezone.utc),
        )

        # Before return: September check
        sept_before = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(len(sept_before), 1)
        self.assertEqual(sept_before[0]["revenue"], Decimal("1000000.00"))
        self.assertEqual(sept_before[0]["orders"], 1)
        self.assertEqual(sept_before[0]["customers"], 1)

        # Return in October
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("1000"),
            refund_amount=Decimal("1000000.00"),
            created_at=datetime(2026, 10, 2, 11, 0, tzinfo=dt_timezone.utc),
        )

        # After return: September report MUST BE 100% UNCHANGED
        sept_after = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(len(sept_after), 1)
        self.assertEqual(sept_after[0]["revenue"], Decimal("1000000.00"))
        self.assertEqual(sept_after[0]["orders"], 1)
        self.assertEqual(sept_after[0]["customers"], 1)

        # October report: reflects negative transaction
        oct_res = BranchService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)
        self.assertEqual(len(oct_res), 1)
        self.assertEqual(oct_res[0]["revenue"], Decimal("-1000000.00"))
        self.assertEqual(oct_res[0]["orders"], 0)
        self.assertEqual(oct_res[0]["customers"], 0)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Full return
    # ─────────────────────────────────────────────────────────────────────────
    def test_05_full_return_in_same_period(self):
        """Full return in same period nets revenue to 0, preserves orders and customers."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100.00"),
            customer=self.cust_ali,
            status=Sale.Status.RETURNED,
            created_at=datetime(2026, 9, 10, 10, 0, tzinfo=dt_timezone.utc),
        )
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("5"),
            refund_amount=Decimal("500.00"),
            created_at=datetime(2026, 9, 15, 14, 0, tzinfo=dt_timezone.utc),
        )

        res = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["revenue"], Decimal("0.00"))
        self.assertEqual(res[0]["orders"], 1)
        self.assertEqual(res[0]["customers"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Returned sale still counted in orders
    # ─────────────────────────────────────────────────────────────────────────
    def test_06_returned_sale_still_counted_in_orders(self):
        """Sale.Status.RETURNED is NOT excluded from orders."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("2"),
            unit_price=Decimal("100.00"),
            status=Sale.Status.RETURNED,
            created_at=self.SEPT_DT,
        )

        res = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(res[0]["orders"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 7. Returned sale customer still counted
    # ─────────────────────────────────────────────────────────────────────────
    def test_07_returned_sale_customer_still_counted(self):
        """Customer of a returned sale is still counted in customers."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("2"),
            unit_price=Decimal("100.00"),
            customer=self.cust_ali,
            status=Sale.Status.RETURNED,
            created_at=self.SEPT_DT,
        )

        res = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(res[0]["customers"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 8. Soft-deleted sale excluded
    # ─────────────────────────────────────────────────────────────────────────
    def test_08_soft_deleted_sale_excluded(self):
        """Soft-deleted sales are completely excluded from branch statistics."""
        deleted_dt = datetime(2026, 9, 16, 12, 0, tzinfo=dt_timezone.utc)
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            customer=self.cust_ali,
            created_at=self.SEPT_DT,
            deleted_at=deleted_dt,
        )

        res = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(len(res), 0)

    # ─────────────────────────────────────────────────────────────────────────
    # 9. Return of deleted sale excluded
    # ─────────────────────────────────────────────────────────────────────────
    def test_09_return_of_deleted_sale_excluded(self):
        """Returns of soft-deleted sales must be excluded."""
        deleted_dt = datetime(2026, 9, 16, 12, 0, tzinfo=dt_timezone.utc)
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            created_at=self.SEPT_DT,
            deleted_at=deleted_dt,
        )
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("10"),
            refund_amount=Decimal("1000.00"),
            created_at=self.OCT_DT,
        )

        res_oct = BranchService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)
        self.assertEqual(len(res_oct), 0)

    # ─────────────────────────────────────────────────────────────────────────
    # 10. store_id filter
    # ─────────────────────────────────────────────────────────────────────────
    def test_10_store_id_filter(self):
        """store_id=X filters only store X."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100.00"),
            created_at=self.SEPT_DT,
        )
        self._create_sale(
            store=self.store_b,
            product=self.prod_oil,
            qty=Decimal("8"),
            unit_price=Decimal("100.00"),
            created_at=self.SEPT_DT,
        )

        res_a = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(len(res_a), 1)
        self.assertEqual(res_a[0]["store_id"], self.store_a.id)
        self.assertEqual(res_a[0]["revenue"], Decimal("500.00"))

        res_b = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_b.id)
        self.assertEqual(len(res_b), 1)
        self.assertEqual(res_b[0]["store_id"], self.store_b.id)
        self.assertEqual(res_b[0]["revenue"], Decimal("800.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 11. Consolidated store mode
    # ─────────────────────────────────────────────────────────────────────────
    def test_11_consolidated_store_mode(self):
        """store_id=None returns all active stores with transactions."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100.00"),
            created_at=self.SEPT_DT,
        )
        self._create_sale(
            store=self.store_b,
            product=self.prod_oil,
            qty=Decimal("8"),
            unit_price=Decimal("100.00"),
            created_at=self.SEPT_DT,
        )

        res = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), None)
        self.assertEqual(len(res), 2)
        store_ids = [r["store_id"] for r in res]
        self.assertIn(self.store_a.id, store_ids)
        self.assertIn(self.store_b.id, store_ids)

    # ─────────────────────────────────────────────────────────────────────────
    # 12. Return-only store
    # ─────────────────────────────────────────────────────────────────────────
    def test_12_return_only_store(self):
        """A store with 0 sales and 500,000 returns appears with revenue = -500,000."""
        # Sale in September at Store A
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100000.00"),
            created_at=self.SEPT_DT,
        )
        # Return in October at Store A
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("5"),
            refund_amount=Decimal("500000.00"),
            created_at=self.OCT_DT,
        )

        # In October, Store A has 0 sales and 500,000 returns
        res_oct = BranchService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)
        self.assertEqual(len(res_oct), 1)
        self.assertEqual(res_oct[0]["store_id"], self.store_a.id)
        self.assertEqual(res_oct[0]["revenue"], Decimal("-500000.00"))
        self.assertEqual(res_oct[0]["orders"], 0)
        self.assertEqual(res_oct[0]["customers"], 0)

    # ─────────────────────────────────────────────────────────────────────────
    # 13. [start, end) boundary
    # ─────────────────────────────────────────────────────────────────────────
    def test_13_date_boundary_half_open(self):
        """23:59:59 is included, next day 00:00:00 is excluded."""
        tz = timezone.get_current_timezone()
        sept30_end = timezone.make_aware(datetime(2026, 9, 30, 23, 59, 59), tz)
        oct01_start = timezone.make_aware(datetime(2026, 10, 1, 0, 0, 0), tz)

        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("1"),
            unit_price=Decimal("100.00"),
            created_at=sept30_end,
        )
        self._create_sale(
            store=self.store_a,
            product=self.prod_filter,
            qty=Decimal("1"),
            unit_price=Decimal("200.00"),
            created_at=oct01_start,
        )

        res_sept = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(len(res_sept), 1)
        self.assertEqual(res_sept[0]["revenue"], Decimal("100.00"))
        self.assertEqual(res_sept[0]["orders"], 1)

        res_oct = BranchService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)
        self.assertEqual(len(res_oct), 1)
        self.assertEqual(res_oct[0]["revenue"], Decimal("200.00"))
        self.assertEqual(res_oct[0]["orders"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 14. SummaryService consistency invariant
    # ─────────────────────────────────────────────────────────────────────────
    def test_14_summary_service_consistency(self):
        """
        SUM(branch.revenue) MUST EXACTLY EQUAL SummaryService.totalRevenue
        for both consolidated and store-filtered scopes across sales and cross-period returns.
        """
        # Store A sales in Sept
        sale_a, item_a = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            created_at=self.SEPT_DT,
        )
        # Store B sales in Sept
        self._create_sale(
            store=self.store_b,
            product=self.prod_oil,
            qty=Decimal("20"),
            unit_price=Decimal("100.00"),
            created_at=self.SEPT_DT,
        )
        # Store A return in Oct
        self._create_return(
            sale=sale_a,
            item=item_a,
            qty=Decimal("4"),
            refund_amount=Decimal("400.00"),
            created_at=self.OCT_DT,
        )
        # Store B sale in Oct
        self._create_sale(
            store=self.store_b,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100.00"),
            created_at=self.OCT_DT,
        )

        # 1. September Consolidated Check
        summary_sept = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), None)
        branch_sept = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), None)
        branch_sept_sum = sum(b["revenue"] for b in branch_sept)
        self.assertEqual(branch_sept_sum, summary_sept["totalRevenue"])
        self.assertEqual(branch_sept_sum, Decimal("3000.00"))

        # 2. September Store A Check
        summary_sept_a = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        branch_sept_a = BranchService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        branch_sept_a_sum = sum(b["revenue"] for b in branch_sept_a)
        self.assertEqual(branch_sept_a_sum, summary_sept_a["totalRevenue"])
        self.assertEqual(branch_sept_a_sum, Decimal("1000.00"))

        # 3. October Consolidated Check
        summary_oct = SummaryService.get(date(2026, 10, 1), date(2026, 10, 31), None)
        branch_oct = BranchService.get(date(2026, 10, 1), date(2026, 10, 31), None)
        branch_oct_sum = sum(b["revenue"] for b in branch_oct)
        self.assertEqual(branch_oct_sum, summary_oct["totalRevenue"])
        # Store B sold 500, Store A returned 400 -> Net = 100
        self.assertEqual(branch_oct_sum, Decimal("100.00"))

        # 4. October Store A Check (Return-only store in Oct)
        summary_oct_a = SummaryService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)
        branch_oct_a = BranchService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)
        branch_oct_a_sum = sum(b["revenue"] for b in branch_oct_a)
        self.assertEqual(branch_oct_a_sum, summary_oct_a["totalRevenue"])
        self.assertEqual(branch_oct_a_sum, Decimal("-400.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 15. API response contract
    # ─────────────────────────────────────────────────────────────────────────
    def test_15_api_response_contract(self):
        """GET /api/v1/reports/ returns branchStatistics matching contract."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("2"),
            unit_price=Decimal("100.00"),
            customer=self.cust_ali,
            created_at=self.SEPT_DT,
        )

        view = ReportsAPIView.as_view()
        req = self.factory.get(f"/api/v1/reports/?store_id={self.store_a.id}&from=2026-09-01&to=2026-09-30")
        force_authenticate(req, user=self.admin_user)
        response = view(req)

        self.assertEqual(response.status_code, 200)
        self.assertIn("branchStatistics", response.data)
        branches = response.data["branchStatistics"]
        self.assertEqual(len(branches), 1)

        b = branches[0]
        expected_keys = {"store_id", "store__name", "revenue", "orders", "customers"}
        self.assertEqual(set(b.keys()), expected_keys)
        self.assertEqual(b["store_id"], self.store_a.id)
        self.assertEqual(b["store__name"], self.store_a.name)
        self.assertEqual(b["revenue"], Decimal("200.00"))
        self.assertEqual(b["orders"], 1)
        self.assertEqual(b["customers"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 16. ExcelExportService integration
    # ─────────────────────────────────────────────────────────────────────────
    def test_16_excel_export_service_integration(self):
        """ExcelExportService must consume branchStatistics without error and generate valid report."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100.00"),
            customer=self.cust_ali,
            created_at=self.SEPT_DT,
        )
        self._create_sale(
            store=self.store_b,
            product=self.prod_oil,
            qty=Decimal("8"),
            unit_price=Decimal("100.00"),
            customer=self.cust_vali,
            created_at=self.SEPT_DT,
        )

        full_data = ReportService.get({
            "from": "2026-09-01",
            "to": "2026-09-30",
            "filter": "custom",
        })
        self.assertIn("branchStatistics", full_data)
        self.assertEqual(len(full_data["branchStatistics"]), 2)

        meta = {"period": "01.09.2026 — 30.09.2026", "store": "Barchasi", "generated": "01.09.2026 12:00"}
        wb_buf = ExcelExportService.generate_report(full_data, meta)
        self.assertGreater(wb_buf.getbuffer().nbytes, 0)
