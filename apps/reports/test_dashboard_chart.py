"""
Regression test suite for Dashboard ChartService under Period Transactional Accounting.

Accounting Invariants:
- SALE STREAM:
    Sale.created_at in [start, end) and Sale.deleted_at IS NULL
    Independent DB aggregation by period granularity (TruncHour, TruncDay, TruncWeek, TruncMonth).

- RETURN STREAM:
    SaleReturn.created_at in [start, end) and SaleReturn.sale.deleted_at IS NULL
    Independent DB aggregation by period granularity.
    Returns reflect negatively in the bucket where the return actually occurred.

- BUCKET MERGING:
    net_value = sold - return
    Cross-period returns MUST NOT retroactively reduce or zero out the original sale's bucket!

- CRITICAL TEST:
    Sep 30 Sale = +1,000,000
    Oct 01 Return = -1,000,000
    Assert: September bucket = +1,000,000, October bucket = -1,000,000.
    September bucket MUST NOT become zero!

- All 5 modes supported: daily, weekly, monthly, yearly, custom.
- Response contract preserved: {"labels": [...], "data": [...]}.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.debts.models import CustomerDebt
from apps.products.models import Product
from apps.reports.services.dashboard_service import (
    ChartService,
    DateRange,
    DateRangeResolver,
    KPIService,
    TopPartsService,
    UZ_MONTHS,
    UZ_WEEKDAYS,
)
from apps.reports.views.dashboard_view import DashboardAPIView
from apps.sales.models import Payment, Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.sales.services.sale_return_service import SaleReturnService
from apps.store.models import Store, StoreUser
from apps.users.models.customers import Customer
from apps.users.models.user import User


class DashboardChartPeriodAccountingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Markaziy do'kon", phone_number="+998901112233")
        cls.store2 = Store.objects.create(name="Filial 2", phone_number="+998902223344")

        cls.seller = User.objects.create(
            phone_number="+998901234567",
            email="seller@example.com",
            is_superuser=True,
            is_staff=True,
        )
        StoreUser.objects.create(user=cls.seller, store=cls.store, is_active=True)
        StoreUser.objects.create(user=cls.seller, store=cls.store2, is_active=True)

        cls.product = Product.objects.create(name="Moy filtri", min_stock=5)
        cls.product2 = Product.objects.create(name="Havo filtri", min_stock=5)

    def setUp(self):
        cache.clear()

    def _create_sale(
        self,
        amount: Decimal,
        dt: datetime,
        store=None,
        paid_amount: Decimal | None = None,
        product=None,
        qty: Decimal = Decimal("1"),
    ) -> tuple[Sale, SaleItem]:
        target_store = store or self.store
        if paid_amount is None:
            paid_amount = amount
        sale = Sale.objects.create(
            store=target_store,
            seller=self.seller,
            total_amount=amount,
            paid_amount=paid_amount,
            status=Sale.Status.PAID if paid_amount == amount else Sale.Status.PARTIAL,
            payment_type=Sale.PaymentType.CASH,
        )
        item = SaleItem.objects.create(
            sale=sale,
            product=product or self.product,
            quantity=qty,
            unit_price=amount / qty,
            purchase_price=Decimal("50000.00"),
            total_price=amount,
        )
        Payment.objects.create(sale=sale, amount=paid_amount, type=Payment.Type.CASH)
        Sale.objects.filter(id=sale.id).update(created_at=dt)
        sale.refresh_from_db()
        item.refresh_from_db()
        return sale, item

    def _create_return(
        self,
        sale: Sale,
        refund_amount: Decimal,
        dt: datetime,
        store=None,
        item=None,
        qty: Decimal = Decimal("1"),
    ) -> tuple[SaleReturn, SaleReturnItem]:
        target_store = store or sale.store
        target_item = item or sale.items.first()
        ret = SaleReturn.objects.create(
            sale=sale,
            store=target_store,
            seller=self.seller,
            total_refund=refund_amount,
        )
        ret_item = SaleReturnItem.objects.create(
            sale_return=ret,
            sale_item=target_item,
            product=target_item.product,
            quantity=qty,
            unit_price=refund_amount / qty,
            total_price=refund_amount,
        )
        SaleReturn.objects.filter(id=ret.id).update(created_at=dt)
        ret.refresh_from_db()
        ret_item.refresh_from_db()
        return ret, ret_item

    # ─────────────────────────────────────────────────────────────
    #  1. PAST YEAR ISOLATION & CURRENT MONTHS (Case 1 & 2)
    # ─────────────────────────────────────────────────────────────
    def test_01_past_year_isolation_and_current_months(self):
        """1. Past year (2025) transactions do not leak into 2026; current months display correctly."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        # 2025 sales
        self._create_sale(Decimal("15000.00"), timezone.make_aware(datetime(2025, 11, 15, 10, 0, 0), tz))
        self._create_sale(Decimal("25000.00"), timezone.make_aware(datetime(2025, 12, 15, 11, 0, 0), tz))

        # 2026 sales
        self._create_sale(Decimal("30000.00"), timezone.make_aware(datetime(2026, 1, 10, 10, 0, 0), tz))
        self._create_sale(Decimal("50000.00"), timezone.make_aware(datetime(2026, 9, 10, 14, 0, 0), tz))
        self._create_sale(Decimal("70000.00"), timezone.make_aware(datetime(2026, 10, 1, 9, 30, 0), tz))

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            labels = res["labels"]
            data = res["data"]

            self.assertEqual(len(labels), 12)
            self.assertEqual(len(data), 12)
            self.assertEqual(data[0], Decimal("30000.00"))   # January
            for idx in range(1, 8):
                self.assertEqual(data[idx], Decimal("0.00"), f"Oy {idx + 1} nol bo'lishi kerak")
            self.assertEqual(data[8], Decimal("50000.00"))   # September
            self.assertEqual(data[9], Decimal("70000.00"))   # October
            self.assertIsNone(data[10])                     # November (future)
            self.assertIsNone(data[11])                     # December (future)

    # ─────────────────────────────────────────────────────────────
    #  2. FUTURE SALES NOT INCLUDED (Case 3)
    # ─────────────────────────────────────────────────────────────
    def test_02_future_sales_not_included(self):
        """2. Future sales after mock_now are not included in the chart."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        self._create_sale(Decimal("99999.00"), timezone.make_aware(datetime(2026, 11, 20, 10, 0, 0), tz))
        self._create_sale(Decimal("88888.00"), timezone.make_aware(datetime(2026, 12, 5, 10, 0, 0), tz))

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            data = res["data"]
            self.assertIsNone(data[10])
            self.assertIsNone(data[11])

    # ─────────────────────────────────────────────────────────────
    #  3. ZERO SALES FOR PASSED MONTHS (Case 4)
    # ─────────────────────────────────────────────────────────────
    def test_03_zero_sales_for_passed_months(self):
        """3. Passed months without activity are 0.00, future months are None."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            data = res["data"]
            for idx in range(10):
                self.assertEqual(data[idx], Decimal("0.00"), f"Oy {idx + 1} 0 bo'lishi kerak")
            self.assertIsNone(data[10])
            self.assertIsNone(data[11])

    # ─────────────────────────────────────────────────────────────
    #  4. FULL RETURN SAME PERIOD (Case A)
    # ─────────────────────────────────────────────────────────────
    def test_04_full_return_same_period(self):
        """4. Sale 999M and full return in same month (October) results in 0.00 for October."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)
        dt_sale = timezone.make_aware(datetime(2026, 10, 1, 9, 0, 0), tz)
        dt_ret = timezone.make_aware(datetime(2026, 10, 1, 11, 0, 0), tz)

        amount = Decimal("999999999.00")
        sale, _ = self._create_sale(amount, dt_sale)
        self._create_return(sale, amount, dt_ret)
        Sale.objects.filter(id=sale.id).update(status=Sale.Status.RETURNED)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            # October (index 9) must be 0.00 (999M - 999M)
            self.assertEqual(res["data"][9], Decimal("0.00"))

            # KPI integration check
            kpi = KPIService.get(store_id="all", dr=dr)
            self.assertEqual(kpi["revenue"], Decimal("0.00"))
            self.assertEqual(kpi["orders"], 1)  # Sale was created in period

    # ─────────────────────────────────────────────────────────────
    #  5. PARTIAL RETURN SAME PERIOD (Case B)
    # ─────────────────────────────────────────────────────────────
    def test_05_partial_return_same_period(self):
        """5. Sale 1,000,000 and return 300,000 in same month results in 700,000."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)
        dt_sale = timezone.make_aware(datetime(2026, 10, 1, 9, 0, 0), tz)
        dt_ret = timezone.make_aware(datetime(2026, 10, 1, 11, 0, 0), tz)

        sale, _ = self._create_sale(Decimal("1000000.00"), dt_sale)
        self._create_return(sale, Decimal("300000.00"), dt_ret)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            self.assertEqual(res["data"][9], Decimal("700000.00"))

    # ─────────────────────────────────────────────────────────────
    #  6. NO RETURN NORMAL SALE (Case C)
    # ─────────────────────────────────────────────────────────────
    def test_06_no_return_normal_sale(self):
        """6. Normal sale with no return shows full amount in month bucket."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)
        dt_sale = timezone.make_aware(datetime(2026, 10, 1, 9, 0, 0), tz)

        self._create_sale(Decimal("1000000.00"), dt_sale)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            self.assertEqual(res["data"][9], Decimal("1000000.00"))

    # ─────────────────────────────────────────────────────────────
    #  7. MULTIPLE SALES WITH FULL AND NO RETURN (Case D)
    # ─────────────────────────────────────────────────────────────
    def test_07_multiple_sales_with_full_and_no_return(self):
        """7. Sale A 1M returned, Sale B 500k kept -> October bucket = 500k."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)
        dt_sale = timezone.make_aware(datetime(2026, 10, 1, 9, 0, 0), tz)
        dt_ret = timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz)

        sale_a, _ = self._create_sale(Decimal("1000000.00"), dt_sale)
        self._create_return(sale_a, Decimal("1000000.00"), dt_ret)
        Sale.objects.filter(id=sale_a.id).update(status=Sale.Status.RETURNED)

        self._create_sale(Decimal("500000.00"), dt_sale, product=self.product2)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            self.assertEqual(res["data"][9], Decimal("500000.00"))

    # ─────────────────────────────────────────────────────────────
    #  8. CRITICAL CROSS-PERIOD FULL RETURN (Sep 30 sale, Oct 1 return)
    # ─────────────────────────────────────────────────────────────
    def test_08_critical_cross_period_full_return(self):
        """
        8. CRITICAL: 2026-09-30 Sale = +1,000,000, 2026-10-01 Return = 1,000,000.
        Period Transactional Accounting Invariant:
        - September (index 8) = +1,000,000.00 (MUST NOT BECOME ZERO!)
        - October (index 9)   = -1,000,000.00 (Reflected negatively in return month!)
        """
        tz = timezone.get_current_timezone()
        dt_sale = timezone.make_aware(datetime(2026, 9, 30, 16, 0, 0), tz)
        dt_return = timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz)
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        sale, _ = self._create_sale(Decimal("1000000.00"), dt_sale)
        self._create_return(sale, Decimal("1000000.00"), dt_return)
        Sale.objects.filter(id=sale.id).update(status=Sale.Status.RETURNED)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")

            self.assertEqual(res["data"][8], Decimal("1000000.00"), "September MUST remain +1,000,000!")
            self.assertEqual(res["data"][9], Decimal("-1000000.00"), "October MUST reflect -1,000,000!")

    # ─────────────────────────────────────────────────────────────
    #  9. CROSS-PERIOD PARTIAL RETURN (Sep 30 sale, Oct 1 return)
    # ─────────────────────────────────────────────────────────────
    def test_09_cross_period_partial_return(self):
        """9. Sep 30 Sale = 1,000,000; Oct 01 Return = 300,000 -> Sep = +1M, Oct = -300k."""
        tz = timezone.get_current_timezone()
        dt_sale = timezone.make_aware(datetime(2026, 9, 30, 16, 0, 0), tz)
        dt_return = timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz)
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        sale, _ = self._create_sale(Decimal("1000000.00"), dt_sale)
        self._create_return(sale, Decimal("300000.00"), dt_return)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")

            self.assertEqual(res["data"][8], Decimal("1000000.00"))
            self.assertEqual(res["data"][9], Decimal("-300000.00"))

    # ─────────────────────────────────────────────────────────────
    #  10. RETURN-ONLY PERIOD
    # ─────────────────────────────────────────────────────────────
    def test_10_return_only_period(self):
        """10. October has no sales, only a return of 500k -> October bucket = -500k."""
        tz = timezone.get_current_timezone()
        dt_aug = timezone.make_aware(datetime(2026, 8, 15, 10, 0, 0), tz)
        dt_oct_ret = timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz)
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        sale, _ = self._create_sale(Decimal("500000.00"), dt_aug)
        self._create_return(sale, Decimal("500000.00"), dt_oct_ret)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")

            self.assertEqual(res["data"][7], Decimal("500000.00"))   # August = +500k
            self.assertEqual(res["data"][8], Decimal("0.00"))         # September = 0
            self.assertEqual(res["data"][9], Decimal("-500000.00"))  # October = -500k

    # ─────────────────────────────────────────────────────────────
    #  11. RETURN-ONLY STORE
    # ─────────────────────────────────────────────────────────────
    def test_11_return_only_store(self):
        """11. Store 1 has only a return in October; Store 2 has a sale."""
        tz = timezone.get_current_timezone()
        dt_aug = timezone.make_aware(datetime(2026, 8, 15, 10, 0, 0), tz)
        dt_oct = timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz)
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        sale1, _ = self._create_sale(Decimal("200000.00"), dt_aug, store=self.store)
        self._create_return(sale1, Decimal("200000.00"), dt_oct, store=self.store)

        self._create_sale(Decimal("600000.00"), dt_oct, store=self.store2)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")

            # Store 1: Oct = -200k
            res1 = ChartService.get(store_id=str(self.store.id), dr=dr, period="yearly")
            self.assertEqual(res1["data"][9], Decimal("-200000.00"))

            # Store 2: Oct = +600k
            res2 = ChartService.get(store_id=str(self.store2.id), dr=dr, period="yearly")
            self.assertEqual(res2["data"][9], Decimal("600000.00"))

            # All stores: Oct = 600k - 200k = +400k
            res_all = ChartService.get(store_id="all", dr=dr, period="yearly")
            self.assertEqual(res_all["data"][9], Decimal("400000.00"))

    # ─────────────────────────────────────────────────────────────
    #  12. SOFT-DELETED SALE
    # ─────────────────────────────────────────────────────────────
    def test_12_soft_deleted_sale(self):
        """12. Soft-deleted sale and its return are excluded from all chart buckets."""
        tz = timezone.get_current_timezone()
        dt_sale = timezone.make_aware(datetime(2026, 10, 1, 9, 0, 0), tz)
        dt_ret = timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz)
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        sale, _ = self._create_sale(Decimal("400000.00"), dt_sale)
        self._create_return(sale, Decimal("150000.00"), dt_ret)
        Sale.all_objects.filter(id=sale.id).update(deleted_at=mock_now)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            self.assertEqual(res["data"][9], Decimal("0.00"))

    # ─────────────────────────────────────────────────────────────
    #  13. MULTIPLE RETURNS
    # ─────────────────────────────────────────────────────────────
    def test_13_multiple_returns(self):
        """13. Multiple returns in same month are summed and deducted correctly."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)
        dt_sale1 = timezone.make_aware(datetime(2026, 10, 1, 8, 0, 0), tz)
        dt_sale2 = timezone.make_aware(datetime(2026, 10, 1, 9, 0, 0), tz)
        dt_ret1 = timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz)
        dt_ret2 = timezone.make_aware(datetime(2026, 10, 1, 11, 0, 0), tz)

        sale1, _ = self._create_sale(Decimal("500000.00"), dt_sale1)
        self._create_return(sale1, Decimal("100000.00"), dt_ret1)

        sale2, _ = self._create_sale(Decimal("700000.00"), dt_sale2)
        self._create_return(sale2, Decimal("200000.00"), dt_ret2)

        # Sales: 1,200,000; Returns: 300,000 -> Net: 900,000
        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            self.assertEqual(res["data"][9], Decimal("900000.00"))

    # ─────────────────────────────────────────────────────────────
    #  14. MULTIPLE STORES FILTERING
    # ─────────────────────────────────────────────────────────────
    def test_14_multiple_stores_filtering(self):
        """14. Store filter isolates chart buckets between stores."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)
        dt_sale = timezone.make_aware(datetime(2026, 10, 1, 9, 0, 0), tz)

        self._create_sale(Decimal("300000.00"), dt_sale, store=self.store)
        self._create_sale(Decimal("800000.00"), dt_sale, store=self.store2)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res1 = ChartService.get(store_id=str(self.store.id), dr=dr, period="yearly")
            res2 = ChartService.get(store_id=str(self.store2.id), dr=dr, period="yearly")

            self.assertEqual(res1["data"][9], Decimal("300000.00"))
            self.assertEqual(res2["data"][9], Decimal("800000.00"))

    # ─────────────────────────────────────────────────────────────
    #  15. DAILY MODE (24 hours)
    # ─────────────────────────────────────────────────────────────
    def test_15_daily_mode(self):
        """15. Daily chart groups by 24 hours (TruncHour); returns deduct in return hour."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 14, 0, 0), tz)
        dt_10 = timezone.make_aware(datetime(2026, 10, 1, 10, 15, 0), tz)
        dt_11 = timezone.make_aware(datetime(2026, 10, 1, 11, 30, 0), tz)

        # Sale at 10:15
        sale, _ = self._create_sale(Decimal("100000.00"), dt_10)
        # Return at 11:30
        self._create_return(sale, Decimal("40000.00"), dt_11)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("daily")
            res = ChartService.get(store_id="all", dr=dr, period="daily")

            self.assertEqual(len(res["labels"]), 24)
            self.assertEqual(len(res["data"]), 24)
            self.assertEqual(res["labels"][10], "10:00")
            self.assertEqual(res["labels"][11], "11:00")

            self.assertEqual(res["data"][10], Decimal("100000.00"))  # Hour 10: sale +100k
            self.assertEqual(res["data"][11], Decimal("-40000.00"))  # Hour 11: return -40k
            self.assertEqual(res["data"][12], Decimal("0.00"))       # Hour 12: 0
            self.assertIsNone(res["data"][15])                      # Hour 15: future -> None

    # ─────────────────────────────────────────────────────────────
    #  16. WEEKLY MODE (7 weekdays)
    # ─────────────────────────────────────────────────────────────
    def test_16_weekly_mode(self):
        """16. Weekly chart groups by weekdays (TruncDay); returns deduct on return day."""
        tz = timezone.get_current_timezone()
        # Monday: 2026-09-28, Wednesday: 2026-09-30
        dt_monday = timezone.make_aware(datetime(2026, 9, 28, 10, 0, 0), tz)
        dt_tuesday = timezone.make_aware(datetime(2026, 9, 29, 11, 0, 0), tz)
        mock_now = timezone.make_aware(datetime(2026, 9, 30, 15, 0, 0), tz)  # Wednesday

        sale, _ = self._create_sale(Decimal("200000.00"), dt_monday)
        self._create_return(sale, Decimal("50000.00"), dt_tuesday)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("weekly")
            res = ChartService.get(store_id="all", dr=dr, period="weekly")

            self.assertEqual(len(res["labels"]), 7)
            self.assertEqual(len(res["data"]), 7)
            self.assertEqual(res["labels"][0], "Dushanba")
            self.assertEqual(res["labels"][1], "Seshanba")
            self.assertEqual(res["labels"][2], "Chorshanba")

            self.assertEqual(res["data"][0], Decimal("200000.00"))   # Monday: +200k
            self.assertEqual(res["data"][1], Decimal("-50000.00"))   # Tuesday: -50k
            self.assertEqual(res["data"][2], Decimal("0.00"))        # Wednesday: 0
            self.assertIsNone(res["data"][3])                       # Thursday: future -> None

    # ─────────────────────────────────────────────────────────────
    #  17. MONTHLY MODE (4 weeks)
    # ── monthly ──
    def test_17_monthly_mode(self):
        """17. Monthly chart groups by 4 weeks."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 15, 12, 0, 0), tz)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("monthly")

            # Week 1 sale, Week 2 return
            dt_w1 = dr.current_from + timedelta(days=2)
            dt_w2 = dr.current_from + timedelta(days=9)

            sale, _ = self._create_sale(Decimal("300000.00"), dt_w1)
            self._create_return(sale, Decimal("100000.00"), dt_w2)

            res = ChartService.get(store_id="all", dr=dr, period="monthly")

            self.assertEqual(res["labels"], ["1-hafta", "2-hafta", "3-hafta", "4-hafta"])
            self.assertEqual(len(res["data"]), 4)
            self.assertEqual(res["data"][0], Decimal("300000.00"))
            self.assertEqual(res["data"][1], Decimal("-100000.00"))

    # ─────────────────────────────────────────────────────────────
    #  18. CUSTOM MODE (Daily points <= 62 days)
    # ─────────────────────────────────────────────────────────────
    def test_18_custom_mode_daily(self):
        """18. Custom range <= 62 days outputs daily points."""
        dr = DateRangeResolver.resolve_custom("2026-09-01", "2026-09-05")
        tz = timezone.get_current_timezone()
        dt_sale = timezone.make_aware(datetime(2026, 9, 2, 10, 0, 0), tz)
        dt_ret = timezone.make_aware(datetime(2026, 9, 3, 10, 0, 0), tz)

        sale, _ = self._create_sale(Decimal("150000.00"), dt_sale)
        self._create_return(sale, Decimal("50000.00"), dt_ret)

        res = ChartService.get(store_id="all", dr=dr, period="custom")

        self.assertEqual(len(res["labels"]), 5)
        self.assertEqual(len(res["data"]), 5)
        self.assertEqual(res["labels"][1], "02.09")
        self.assertEqual(res["labels"][2], "03.09")
        self.assertEqual(res["data"][1], Decimal("150000.00"))  # 02.09: +150k
        self.assertEqual(res["data"][2], Decimal("-50000.00"))  # 03.09: -50k

    # ─────────────────────────────────────────────────────────────
    #  19. CUSTOM MODE (Monthly points > 62 days)
    # ─────────────────────────────────────────────────────────────
    def test_19_custom_mode_monthly(self):
        """19. Custom range > 62 days outputs monthly points."""
        dr = DateRangeResolver.resolve_custom("2026-01-01", "2026-04-30")
        tz = timezone.get_current_timezone()
        dt_sale = timezone.make_aware(datetime(2026, 1, 15, 10, 0, 0), tz)
        dt_ret = timezone.make_aware(datetime(2026, 2, 15, 10, 0, 0), tz)

        sale, _ = self._create_sale(Decimal("500000.00"), dt_sale)
        self._create_return(sale, Decimal("100000.00"), dt_ret)

        res = ChartService.get(store_id="all", dr=dr, period="custom")

        self.assertEqual(len(res["labels"]), 4)  # Yanvar .. Aprel
        self.assertEqual(res["data"][0], Decimal("500000.00"))   # January: +500k
        self.assertEqual(res["data"][1], Decimal("-100000.00"))  # February: -100k

    # ─────────────────────────────────────────────────────────────
    #  20. EMPTY PERIOD
    # ─────────────────────────────────────────────────────────────
    def test_20_empty_period(self):
        """20. Empty period returns correct structure with zeros and Nones."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")

            for m in range(10):
                self.assertEqual(res["data"][m], Decimal("0.00"))
            self.assertIsNone(res["data"][10])
            self.assertIsNone(res["data"][11])

    # ─────────────────────────────────────────────────────────────
    #  21. EXISTING API RESPONSE CONTRACT
    # ─────────────────────────────────────────────────────────────
    def test_21_existing_chart_api_response_contract(self):
        """21. Response structure strictly contains 'labels' (list of str) and 'data' (list)."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")

            self.assertIsInstance(res, dict)
            self.assertEqual(set(res.keys()), {"labels", "data"})
            self.assertIsInstance(res["labels"], list)
            self.assertIsInstance(res["data"], list)
            for label in res["labels"]:
                self.assertIsInstance(label, str)

    # ─────────────────────────────────────────────────────────────
    #  22. TOP PARTS SERVICE WITH RETURNS
    # ─────────────────────────────────────────────────────────────
    def test_22_top_parts_service_with_returns(self):
        """22. TopPartsService: Product A has 10 sold, 4 returned -> sold=6. Product B 5 sold, 5 returned -> excluded."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)
        dt_sale = timezone.make_aware(datetime(2026, 10, 1, 8, 0, 0), tz)
        dt_ret = timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz)

        # Sale 1: Product A (10 pcs x 100,000)
        sale1, item1 = self._create_sale(Decimal("1000000.00"), dt_sale, product=self.product, qty=Decimal("10"))
        self._create_return(sale1, Decimal("400000.00"), dt_ret, item=item1, qty=Decimal("4"))

        # Sale 2: Product B (5 pcs x 50,000 = 250,000, fully returned)
        sale2, item2 = self._create_sale(Decimal("250000.00"), dt_sale, product=self.product2, qty=Decimal("5"))
        self._create_return(sale2, Decimal("250000.00"), dt_ret, item=item2, qty=Decimal("5"))

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            top_parts = TopPartsService.get(store_id="all", dr=dr)

            self.assertEqual(len(top_parts), 1)
            self.assertEqual(top_parts[0]["name"], self.product.name)
            self.assertEqual(top_parts[0]["sold"], Decimal("6.00"))
            self.assertEqual(top_parts[0]["rev"], Decimal("600000.00"))

    # ─────────────────────────────────────────────────────────────
    #  23. NET PAID AND NET DEBT CONSISTENCY
    # ─────────────────────────────────────────────────────────────
    def test_23_net_paid_and_net_debt_mathematical_consistency(self):
        """23. Paid and debt consistency: 1M sale (600k paid, 400k debt) with 300k return reducing debt."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)
        dt_sale = timezone.make_aware(datetime(2026, 10, 1, 8, 0, 0), tz)
        dt_ret = timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz)

        customer = Customer.objects.create(full_name="Qarzli mijoz", phone_number="+998909876543")

        sale = Sale.objects.create(
            store=self.store,
            seller=self.seller,
            customer=customer,
            total_amount=Decimal("1000000.00"),
            paid_amount=Decimal("600000.00"),
            status=Sale.Status.PARTIAL,
            payment_type=Sale.PaymentType.MIXED,
        )
        Sale.objects.filter(id=sale.id).update(created_at=dt_sale)
        item = SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=Decimal("10"),
            unit_price=Decimal("100000.00"),
            purchase_price=Decimal("50000.00"),
            total_price=Decimal("1000000.00"),
        )
        Payment.objects.create(sale=sale, customer=customer, amount=Decimal("600000.00"), type=Payment.Type.CASH)
        CustomerDebt.objects.create(
            customer=customer,
            sale=sale,
            amount=Decimal("400000.00"),
            type=CustomerDebt.Type.INCREASE,
        )

        self._create_return(sale, Decimal("300000.00"), dt_ret, item=item, qty=Decimal("3"))

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            kpi = KPIService.get(store_id="all", dr=dr)

            self.assertEqual(kpi["revenue"], Decimal("700000.00"))
            self.assertEqual(kpi["paid"], Decimal("600000.00"))
            self.assertEqual(kpi["debt"], Decimal("100000.00"))
            self.assertEqual(kpi["revenue"], kpi["paid"] + kpi["debt"])

    # ─────────────────────────────────────────────────────────────
    #  24. DASHBOARD API VIEW YEARLY INTEGRATION
    # ─────────────────────────────────────────────────────────────
    def test_24_api_view_yearly_integration(self):
        """24. GET /api/reports/dashboard/ returns chart and kpi matching Period Transactional semantics."""
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        # 2025 sale (past year)
        self._create_sale(Decimal("10000.00"), timezone.make_aware(datetime(2025, 11, 20, 10, 0, 0), tz))
        # 2026-09 and 2026-10 sales
        self._create_sale(Decimal("45000.00"), timezone.make_aware(datetime(2026, 9, 15, 10, 0, 0), tz))
        self._create_sale(Decimal("65000.00"), timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz))

        factory = APIRequestFactory()
        request = factory.get("/api/reports/dashboard/", {"period": "yearly", "store_id": "all"})
        force_authenticate(request, user=self.seller)

        with patch("django.utils.timezone.now", return_value=mock_now):
            response = DashboardAPIView.as_view()(request)
            self.assertEqual(response.status_code, 200)

            chart = response.data["chart"]
            self.assertIn("labels", chart)
            self.assertIn("data", chart)

            labels = chart["labels"]
            data = chart["data"]

            self.assertEqual(labels, [
                "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
                "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"
            ])
            self.assertEqual(data[8], Decimal("45000.00"))  # September
            self.assertEqual(data[9], Decimal("65000.00"))  # October
            self.assertIsNone(data[10])                     # November
            self.assertIsNone(data[11])                     # December

            # KPI
            kpi = response.data["kpi"]
            self.assertEqual(kpi["revenue"], Decimal("110000.00"))  # 45k + 65k
            self.assertEqual(kpi["orders"], 2)
