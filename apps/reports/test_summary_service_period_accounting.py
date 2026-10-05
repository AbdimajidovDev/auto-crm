"""
Regression test suite for SummaryService under Period Transactional Accounting.

Accounting Invariants:
1. Sale stream: Sale.created_at ∈ [start, end), deleted_at IS NULL, store_id filter.
   - sold_revenue = SUM(Sale.total_amount)
   - sold_cogs = SUM(SaleItem.quantity * SaleItem.purchase_price)
   - sold_profit = sold_revenue - sold_cogs
   - total_orders = Count(Sale.id) (Status.RETURNED is NOT excluded)
   - total_customers = Count(distinct customer)

2. Return stream: SaleReturn.created_at ∈ [start, end), sale__deleted_at IS NULL, store_id filter.
   - return_revenue = SUM(SaleReturn.total_refund) (or SaleReturnItem.total_price)
   - return_cogs = SUM(SaleReturnItem.quantity * SaleReturnItem.sale_item.purchase_price)
   - return_profit = return_revenue - return_cogs

3. Period Transactional Netting:
   - totalRevenue = sold_revenue - return_revenue
   - totalProfit = sold_profit - return_profit
   - totalExpenses = totalRevenue - totalProfit (Net COGS)
   - averageOrderValue = round(totalRevenue / totalOrders, 2) if totalOrders else Decimal("0")

4. Cross-period invariant:
   - Sale in Sept 2026: +1,000,000 rev, +300,000 profit.
   - Full return in Oct 2026: -1,000,000 rev, -300,000 profit.
   - Sept report remains 100% unchanged after Oct return.
   - Oct report reflects -1,000,000 rev, -300,000 profit.
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.products.models import Category, Product
from apps.reports.services.excel_export_service import ExcelExportService
from apps.reports.services.report_service import ReportService, SummaryService
from apps.reports.views.report_view import ReportsAPIView
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store
from apps.users.models.customers import Customer
from apps.users.models.user import User


class SummaryServicePeriodAccountingTest(TestCase):
    SEPT_DT = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
    OCT_DT = datetime(2026, 10, 5, 12, 0, tzinfo=dt_timezone.utc)

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

        cls.prod_oil = Product.objects.create(
            name="Motor Oil 5W-40",
            sku="OIL-001",
            barcode="666600000001",
            category=cls.cat_parts,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_filter = Product.objects.create(
            name="Oil Filter",
            sku="FLT-001",
            barcode="666600000002",
            category=cls.cat_parts,
            status=Product.ProductStatus.ACTIVE,
        )

        cls.cust_ali = Customer.objects.create(full_name="Ali Valiyev", phone_number="+998901234567")
        cls.cust_vali = Customer.objects.create(full_name="Vali Aliyev", phone_number="+998909876543")

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
        """Single sale in period: revenue, profit, expenses, orders, customers, averageOrderValue."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("70.00"),
            customer=self.cust_ali,
            created_at=self.SEPT_DT,
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)

        self.assertEqual(res["totalRevenue"], Decimal("1000.00"))
        self.assertEqual(res["totalProfit"], Decimal("300.00"))
        self.assertEqual(res["totalExpenses"], Decimal("700.00"))  # COGS = 700
        self.assertEqual(res["totalOrders"], 1)
        self.assertEqual(res["totalCustomers"], 1)
        self.assertEqual(res["averageOrderValue"], Decimal("1000.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Same-period return
    # ─────────────────────────────────────────────────────────────────────────
    def test_02_same_period_return(self):
        """Sale and return both occur in September: return is subtracted properly."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("70.00"),
            customer=self.cust_ali,
            created_at=datetime(2026, 9, 10, 10, 0, tzinfo=dt_timezone.utc),
        )
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("4"),
            refund_amount=Decimal("400.00"),
            created_at=datetime(2026, 9, 20, 14, 0, tzinfo=dt_timezone.utc),
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)

        # Revenue: 1000 - 400 = 600
        self.assertEqual(res["totalRevenue"], Decimal("600.00"))
        # Profit: (1000 - 700) - (400 - 280) = 300 - 120 = 180
        self.assertEqual(res["totalProfit"], Decimal("180.00"))
        # Expenses (COGS): 600 - 180 = 420 (which is 6 sold * 70 = 420)
        self.assertEqual(res["totalExpenses"], Decimal("420.00"))
        # Orders and customers still 1
        self.assertEqual(res["totalOrders"], 1)
        self.assertEqual(res["totalCustomers"], 1)
        self.assertEqual(res["averageOrderValue"], Decimal("600.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Cross-period return (Core Period Transactional Invariant)
    # ─────────────────────────────────────────────────────────────────────────
    def test_03_cross_period_return_invariant(self):
        """
        Cross-period invariant:
        September sale: +1,000,000 revenue, +300,000 profit.
        October full return: -1,000,000 revenue, -300,000 profit.

        September report MUST NOT change after October return is recorded.
        October report MUST reflect negative return transaction.
        """
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("1000"),
            unit_price=Decimal("1000.00"),
            purchase_price=Decimal("700.00"),
            customer=self.cust_ali,
            created_at=datetime(2026, 9, 30, 18, 0, tzinfo=dt_timezone.utc),
        )

        # Pre-return check for September
        sept_before = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(sept_before["totalRevenue"], Decimal("1000000.00"))
        self.assertEqual(sept_before["totalProfit"], Decimal("300000.00"))
        self.assertEqual(sept_before["totalExpenses"], Decimal("700000.00"))
        self.assertEqual(sept_before["totalOrders"], 1)
        self.assertEqual(sept_before["totalCustomers"], 1)

        # Full return in October
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("1000"),
            refund_amount=Decimal("1000000.00"),
            created_at=datetime(2026, 10, 1, 10, 0, tzinfo=dt_timezone.utc),
        )

        # Post-return check for September: MUST BE 100% UNCHANGED
        sept_after = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(sept_after["totalRevenue"], Decimal("1000000.00"))
        self.assertEqual(sept_after["totalProfit"], Decimal("300000.00"))
        self.assertEqual(sept_after["totalExpenses"], Decimal("7000000.00") / 10)  # 700000.00
        self.assertEqual(sept_after["totalOrders"], 1)
        self.assertEqual(sept_after["totalCustomers"], 1)

        # Check for October: reflects negative transaction
        oct_res = SummaryService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)
        self.assertEqual(oct_res["totalRevenue"], Decimal("-1000000.00"))
        self.assertEqual(oct_res["totalProfit"], Decimal("-300000.00"))
        self.assertEqual(oct_res["totalExpenses"], Decimal("-700000.00"))
        self.assertEqual(oct_res["totalOrders"], 0)
        self.assertEqual(oct_res["totalCustomers"], 0)
        self.assertEqual(oct_res["averageOrderValue"], Decimal("0"))

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Full return
    # ─────────────────────────────────────────────────────────────────────────
    def test_04_full_return_in_same_period(self):
        """Full return in same period nets revenue and profit to 0, preserves totalOrders."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            customer=self.cust_ali,
            status=Sale.Status.RETURNED,  # Marked as RETURNED
            created_at=datetime(2026, 9, 5, 10, 0, tzinfo=dt_timezone.utc),
        )
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("5"),
            refund_amount=Decimal("500.00"),
            created_at=datetime(2026, 9, 10, 15, 0, tzinfo=dt_timezone.utc),
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)

        self.assertEqual(res["totalRevenue"], Decimal("0.00"))
        self.assertEqual(res["totalProfit"], Decimal("0.00"))
        self.assertEqual(res["totalExpenses"], Decimal("0.00"))
        # In Period Transactional accounting, the order placed in Sept is still an order
        self.assertEqual(res["totalOrders"], 1)
        self.assertEqual(res["totalCustomers"], 1)
        self.assertEqual(res["averageOrderValue"], Decimal("0.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Partial return
    # ─────────────────────────────────────────────────────────────────────────
    def test_05_partial_return(self):
        """Partial return only deducts the returned quantity and refund amount."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("200.00"),
            purchase_price=Decimal("120.00"),
            customer=self.cust_vali,
            created_at=self.SEPT_DT,
        )
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("3"),
            refund_amount=Decimal("600.00"),
            created_at=datetime(2026, 9, 20, 12, 0, tzinfo=dt_timezone.utc),
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)

        # Revenue: 2000 - 600 = 1400
        self.assertEqual(res["totalRevenue"], Decimal("1400.00"))
        # Sold profit = 2000 - 1200 = 800
        # Return profit = 600 - (3 * 120) = 600 - 360 = 240
        # Net profit = 800 - 240 = 560
        self.assertEqual(res["totalProfit"], Decimal("560.00"))
        self.assertEqual(res["totalExpenses"], Decimal("840.00"))  # 1400 - 560 = 840 (7 * 120)
        self.assertEqual(res["totalOrders"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Returned sale still counted in totalOrders
    # ─────────────────────────────────────────────────────────────────────────
    def test_06_returned_sale_still_counted_in_total_orders(self):
        """Status.RETURNED must NOT be excluded from totalOrders in sale period."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("1"),
            unit_price=Decimal("100.00"),
            status=Sale.Status.RETURNED,
            created_at=self.SEPT_DT,
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(res["totalOrders"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 7. Returned sale customer still counted in totalCustomers
    # ─────────────────────────────────────────────────────────────────────────
    def test_07_returned_sale_customer_still_counted(self):
        """Customer with only a returned sale must still be counted in totalCustomers."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("1"),
            unit_price=Decimal("100.00"),
            customer=self.cust_ali,
            status=Sale.Status.RETURNED,
            created_at=self.SEPT_DT,
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(res["totalCustomers"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 8. Soft-deleted sale excluded
    # ─────────────────────────────────────────────────────────────────────────
    def test_08_soft_deleted_sale_excluded(self):
        """Soft-deleted sale and its items must not leak into revenue, profit, or order count."""
        deleted_dt = datetime(2026, 9, 16, 12, 0, tzinfo=dt_timezone.utc)
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("70.00"),
            customer=self.cust_ali,
            created_at=self.SEPT_DT,
            deleted_at=deleted_dt,
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(res["totalRevenue"], Decimal("0.00"))
        self.assertEqual(res["totalProfit"], Decimal("0.00"))
        self.assertEqual(res["totalExpenses"], Decimal("0.00"))
        self.assertEqual(res["totalOrders"], 0)
        self.assertEqual(res["totalCustomers"], 0)

    # ─────────────────────────────────────────────────────────────────────────
    # 9. Return of deleted sale excluded
    # ─────────────────────────────────────────────────────────────────────────
    def test_09_return_of_deleted_sale_excluded(self):
        """Returns linked to soft-deleted sales must be excluded."""
        deleted_dt = datetime(2026, 9, 16, 12, 0, tzinfo=dt_timezone.utc)
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("70.00"),
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

        # October report should not include the return of a deleted sale
        res = SummaryService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)
        self.assertEqual(res["totalRevenue"], Decimal("0.00"))
        self.assertEqual(res["totalProfit"], Decimal("0.00"))
        self.assertEqual(res["totalExpenses"], Decimal("0.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 10. Store filtering
    # ─────────────────────────────────────────────────────────────────────────
    def test_10_store_filter_isolation(self):
        """Store filter isolates Store A from Store B sales and returns."""
        # Store A sale
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            created_at=self.SEPT_DT,
        )
        # Store B sale
        self._create_sale(
            store=self.store_b,
            product=self.prod_oil,
            qty=Decimal("8"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            created_at=self.SEPT_DT,
        )

        res_a = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        res_b = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_b.id)

        self.assertEqual(res_a["totalRevenue"], Decimal("500.00"))
        self.assertEqual(res_a["totalProfit"], Decimal("200.00"))
        self.assertEqual(res_a["totalOrders"], 1)

        self.assertEqual(res_b["totalRevenue"], Decimal("800.00"))
        self.assertEqual(res_b["totalProfit"], Decimal("320.00"))
        self.assertEqual(res_b["totalOrders"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 11. Consolidated stores (store_id=None)
    # ─────────────────────────────────────────────────────────────────────────
    def test_11_consolidated_stores(self):
        """When store_id is None, data is consolidated across all stores."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            created_at=self.SEPT_DT,
        )
        self._create_sale(
            store=self.store_b,
            product=self.prod_oil,
            qty=Decimal("8"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            created_at=self.SEPT_DT,
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), None)
        self.assertEqual(res["totalRevenue"], Decimal("1300.00"))
        self.assertEqual(res["totalProfit"], Decimal("520.00"))
        self.assertEqual(res["totalOrders"], 2)

    # ─────────────────────────────────────────────────────────────────────────
    # 12. DEBT sale included
    # ─────────────────────────────────────────────────────────────────────────
    def test_12_debt_sale_included(self):
        """DEBT sales are included under accrual accounting."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("4"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("50.00"),
            status=Sale.Status.DEBT,
            customer=self.cust_ali,
            created_at=self.SEPT_DT,
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(res["totalRevenue"], Decimal("400.00"))
        self.assertEqual(res["totalProfit"], Decimal("200.00"))
        self.assertEqual(res["totalOrders"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 13. Date boundary [start, end)
    # ─────────────────────────────────────────────────────────────────────────
    def test_13_date_boundary_half_open(self):
        """Half-open interval [start, end): 23:59:59 is included, next day 00:00:00 is excluded."""
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

        res_sept = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(res_sept["totalRevenue"], Decimal("100.00"))
        self.assertEqual(res_sept["totalOrders"], 1)

        res_oct = SummaryService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)
        self.assertEqual(res_oct["totalRevenue"], Decimal("200.00"))
        self.assertEqual(res_oct["totalOrders"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 14. Profit based on historical purchase_price
    # ─────────────────────────────────────────────────────────────────────────
    def test_14_profit_based_on_historical_purchase_price(self):
        """Profit must strictly use SaleItem.purchase_price snapshot."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("150.00"),
            purchase_price=Decimal("95.00"),  # COGS = 950
            created_at=self.SEPT_DT,
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(res["totalRevenue"], Decimal("1500.00"))
        self.assertEqual(res["totalProfit"], Decimal("550.00"))  # 1500 - 950 = 550
        self.assertEqual(res["totalExpenses"], Decimal("950.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 15. Return COGS based on original SaleItem.purchase_price
    # ─────────────────────────────────────────────────────────────────────────
    def test_15_return_cogs_based_on_sale_item_purchase_price(self):
        """Return profit must calculate return_cogs = qty * sale_item.purchase_price."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("40.00"),
            created_at=self.SEPT_DT,
        )
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("3"),
            refund_amount=Decimal("300.00"),
            created_at=self.OCT_DT,
        )

        # In October: return_revenue = 300, return_cogs = 3 * 40 = 120
        # return_profit = 300 - 120 = 180
        # totalProfit in Oct = 0 - 180 = -180
        res_oct = SummaryService.get(date(2026, 10, 1), date(2026, 10, 31), self.store_a.id)
        self.assertEqual(res_oct["totalRevenue"], Decimal("-300.00"))
        self.assertEqual(res_oct["totalProfit"], Decimal("-180.00"))
        self.assertEqual(res_oct["totalExpenses"], Decimal("-120.00"))  # -300 - (-180) = -120

    # ─────────────────────────────────────────────────────────────────────────
    # 16. averageOrderValue
    # ─────────────────────────────────────────────────────────────────────────
    def test_16_average_order_value_calculation(self):
        """averageOrderValue = round(totalRevenue / totalOrders, 2) if totalOrders else Decimal("0")."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("1"),
            unit_price=Decimal("100.00"),
            created_at=self.SEPT_DT,
        )
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("1"),
            unit_price=Decimal("200.00"),
            created_at=self.SEPT_DT,
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(res["totalRevenue"], Decimal("300.00"))
        self.assertEqual(res["totalOrders"], 2)
        self.assertEqual(res["averageOrderValue"], Decimal("150.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 17. API response contract
    # ─────────────────────────────────────────────────────────────────────────
    def test_17_api_response_contract(self):
        """GET /api/v1/reports/ must return the summary dict with exact keys and types."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("2"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            customer=self.cust_ali,
            created_at=self.SEPT_DT,
        )

        view = ReportsAPIView.as_view()
        req = self.factory.get(f"/api/v1/reports/?store_id={self.store_a.id}&from=2026-09-01&to=2026-09-30")
        force_authenticate(req, user=self.admin_user)
        response = view(req)

        self.assertEqual(response.status_code, 200)
        self.assertIn("summary", response.data)
        summary = response.data["summary"]

        expected_keys = {
            "totalRevenue",
            "totalProfit",
            "totalExpenses",
            "totalOrders",
            "averageOrderValue",
            "totalCustomers",
        }
        self.assertEqual(set(summary.keys()), expected_keys)
        self.assertEqual(summary["totalRevenue"], Decimal("200.00"))
        self.assertEqual(summary["totalProfit"], Decimal("80.00"))
        self.assertEqual(summary["totalExpenses"], Decimal("120.00"))
        self.assertEqual(summary["totalOrders"], 1)
        self.assertEqual(summary["totalCustomers"], 1)
        self.assertEqual(summary["averageOrderValue"], Decimal("200.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 18. ExcelExportService integration
    # ─────────────────────────────────────────────────────────────────────────
    def test_18_excel_export_service_integration(self):
        """ExcelExportService must consume the summary KPI tiles without error."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            customer=self.cust_ali,
            created_at=self.SEPT_DT,
        )

        full_data = ReportService.get({
            "store_id": str(self.store_a.id),
            "from": "2026-09-01",
            "to": "2026-09-30",
            "filter": "custom",
        })
        meta = {"period": "01.09.2026 — 30.09.2026", "store": "Store A", "generated": "01.09.2026 12:00"}
        wb_buf = ExcelExportService.generate_report(full_data, meta)
        self.assertGreater(wb_buf.getbuffer().nbytes, 0)

    # ─────────────────────────────────────────────────────────────────────────
    # 19. Cheque-level discount handling
    # ─────────────────────────────────────────────────────────────────────────
    def test_19_cheque_level_discount_handling(self):
        """Sale with discount: revenue = items_total - discount, profit = revenue - COGS."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("70.00"),  # COGS = 700
            discount_amount=Decimal("100.00"),  # total_amount = 1000 - 100 = 900
            customer=self.cust_ali,
            created_at=self.SEPT_DT,
        )

        res = SummaryService.get(date(2026, 9, 1), date(2026, 9, 30), self.store_a.id)
        self.assertEqual(res["totalRevenue"], Decimal("900.00"))
        self.assertEqual(res["totalProfit"], Decimal("200.00"))  # 900 - 700 = 200
        self.assertEqual(res["totalExpenses"], Decimal("700.00"))  # COGS = 700
        self.assertEqual(res["totalOrders"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 20. Zero activity period
    # ─────────────────────────────────────────────────────────────────────────
    def test_20_zero_activity_period(self):
        """When there are no sales and no returns, all metrics must be zero."""
        res = SummaryService.get(date(2025, 1, 1), date(2025, 1, 31), self.store_a.id)
        self.assertEqual(res["totalRevenue"], Decimal("0.00"))
        self.assertEqual(res["totalProfit"], Decimal("0.00"))
        self.assertEqual(res["totalExpenses"], Decimal("0.00"))
        self.assertEqual(res["totalOrders"], 0)
        self.assertEqual(res["totalCustomers"], 0)
        self.assertEqual(res["averageOrderValue"], Decimal("0"))
