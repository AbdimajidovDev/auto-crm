"""
Regression test suite for ReportBuilder Customers report under Period Transactional Accounting.

Business Semantics:
- Customers report = PERIOD TRANSACTIONAL ACCOUNTING.
- period_purchases = (sales in selected period) - (returns in selected period).
- Cross-period returns do NOT retrospectively alter past closed sale periods.
- Soft-deleted sales and their returns are strictly excluded.
- Both SaleReturn.customer and Sale.customer are safely respected without duplication.
- No Cartesian multiplication when multiple sales and returns exist.
- Search, has_debt, store, and date range filters are preserved.
- API response contract (columns, rows, summary) is preserved.
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase

from apps.debts.models import CustomerDebt
from apps.reports.services.report_builder import ReportBuilderService, _build_customers
from apps.sales.models import Sale, SaleReturn
from apps.store.models import Store
from apps.users.models.customers import Customer
from apps.users.models.user import User


class CustomersReportPeriodAccountingTest(TestCase):
    DEFAULT_DT = datetime(2026, 1, 15, 12, 0, tzinfo=dt_timezone.utc)

    @classmethod
    def setUpTestData(cls):
        cls.store_a = Store.objects.create(name="Store A", phone_number="+998901111111")
        cls.store_b = Store.objects.create(name="Store B", phone_number="+998902222222")
        cls.user = User.objects.create(
            phone_number="+998903333333",
            email="seller@example.com",
            is_staff=True,
            is_superuser=True,
        )

    def _create_customer(self, name="Ali Valiyev", phone="+998901234567"):
        return Customer.objects.create(full_name=name, phone_number=phone)

    def _create_sale(
        self,
        customer,
        store=None,
        amount=Decimal("100.00"),
        created_at=None,
        deleted_at=None,
    ):
        sale = Sale.objects.create(
            store=store or self.store_a,
            seller=self.user,
            customer=customer,
            total_amount=amount,
            paid_amount=amount,
            status=Sale.Status.PAID,
        )
        target_dt = created_at or self.DEFAULT_DT
        Sale.objects.filter(id=sale.id).update(created_at=target_dt, deleted_at=deleted_at)
        sale.refresh_from_db()
        return sale

    def _create_return(
        self,
        sale,
        amount=Decimal("40.00"),
        store=None,
        customer=None,
        created_at=None,
    ):
        ret = SaleReturn.objects.create(
            sale=sale,
            store=store or sale.store,
            customer=customer,
            seller=self.user,
            total_refund=amount,
        )
        target_dt = created_at or self.DEFAULT_DT
        SaleReturn.objects.filter(id=ret.id).update(created_at=target_dt)
        ret.refresh_from_db()
        return ret

    def _get_customer_row(self, data, customer_id):
        for r in data["rows"]:
            c = Customer.objects.filter(full_name=r["name"]).first()
            if c and c.id == customer_id:
                return r
        return None

    def test_1_normal_sale(self):
        """1. Normal sale: 100 -> purchases 100"""
        c = self._create_customer("Customer 1", "+998901110001")
        self._create_sale(c, amount=Decimal("100.00"))

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        row = self._get_customer_row(data, c.id)
        self.assertIsNotNone(row)
        self.assertEqual(row["purchases"], "100.00")

    def test_2_same_period_partial_return(self):
        """2. Same-period partial return: 100 - 40 = 60"""
        c = self._create_customer("Customer 2", "+998901110002")
        s = self._create_sale(c, amount=Decimal("100.00"))
        self._create_return(s, amount=Decimal("40.00"), customer=c)

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        row = self._get_customer_row(data, c.id)
        self.assertIsNotNone(row)
        self.assertEqual(row["purchases"], "60.00")

    def test_3_same_period_full_return(self):
        """3. Same-period full return: 100 - 100 = 0"""
        c = self._create_customer("Customer 3", "+998901110003")
        s = self._create_sale(c, amount=Decimal("100.00"))
        self._create_return(s, amount=Decimal("100.00"), customer=c)

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        row = self._get_customer_row(data, c.id)
        self.assertIsNotNone(row)
        self.assertEqual(row["purchases"], "0.00")

    def test_4_cross_period_return(self):
        """
        4. Cross-period:
           Sep 30 sale 1000 -> Sep report 1000
           Oct 1 return 1000 -> Oct report -1000
           Sep report re-checked: still 1000 (retrospective integrity preserved)
           Sep+Oct combined report: 0
        """
        c = self._create_customer("Customer 4", "+998901110004")
        dt_sep = datetime(2026, 9, 30, 15, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 1, 10, 0, tzinfo=dt_timezone.utc)

        s = self._create_sale(c, amount=Decimal("1000.00"), created_at=dt_sep)
        self._create_return(s, amount=Decimal("1000.00"), customer=c, created_at=dt_oct)

        # September Report
        data_sep = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-09-01",
            "to": "2026-09-30",
        }, self.user)
        row_sep = self._get_customer_row(data_sep, c.id)
        self.assertEqual(row_sep["purchases"], "1000.00")

        # October Report
        data_oct = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-10-01",
            "to": "2026-10-31",
        }, self.user)
        row_oct = self._get_customer_row(data_oct, c.id)
        self.assertEqual(row_oct["purchases"], "-1000.00")

        # Combined Sep + Oct Report
        data_all = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-09-01",
            "to": "2026-10-31",
        }, self.user)
        row_all = self._get_customer_row(data_all, c.id)
        self.assertEqual(row_all["purchases"], "0.00")

    def test_5_return_only_period(self):
        """5. Return-only period: 0 - 300 = -300"""
        c = self._create_customer("Customer 5", "+998901110005")
        dt_old = datetime(2025, 12, 1, 12, 0, tzinfo=dt_timezone.utc)
        dt_jan = datetime(2026, 1, 10, 12, 0, tzinfo=dt_timezone.utc)

        s = self._create_sale(c, amount=Decimal("300.00"), created_at=dt_old)
        self._create_return(s, amount=Decimal("300.00"), customer=c, created_at=dt_jan)

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        row = self._get_customer_row(data, c.id)
        self.assertIsNotNone(row)
        self.assertEqual(row["purchases"], "-300.00")

    def test_6_soft_deleted_sale_excluded(self):
        """6. Soft-deleted sale excluded from period_purchases"""
        c = self._create_customer("Customer 6", "+998901110006")
        dt_del = datetime(2026, 1, 20, 12, 0, tzinfo=dt_timezone.utc)
        self._create_sale(c, amount=Decimal("500.00"), deleted_at=dt_del)

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        row = self._get_customer_row(data, c.id)
        self.assertIsNotNone(row)
        self.assertEqual(row["purchases"], "0.00")

    def test_7_soft_deleted_sale_return_excluded(self):
        """7. Soft-deleted sale's return excluded from period_returns"""
        c = self._create_customer("Customer 7", "+998901110007")
        dt_del = datetime(2026, 1, 20, 12, 0, tzinfo=dt_timezone.utc)
        s = self._create_sale(c, amount=Decimal("500.00"), deleted_at=dt_del)
        self._create_return(s, amount=Decimal("200.00"), customer=c)

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        row = self._get_customer_row(data, c.id)
        self.assertIsNotNone(row)
        self.assertEqual(row["purchases"], "0.00")

    def test_8_salereturn_customer_null_resolves_via_sale_customer(self):
        """8. SaleReturn.customer = NULL resolves correctly to sale.customer"""
        c = self._create_customer("Customer 8", "+998901110008")
        s = self._create_sale(c, amount=Decimal("200.00"))
        # customer explicitly None on SaleReturn
        self._create_return(s, amount=Decimal("70.00"), customer=None)

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        row = self._get_customer_row(data, c.id)
        self.assertIsNotNone(row)
        self.assertEqual(row["purchases"], "130.00")  # 200 - 70

    def test_9_salereturn_customer_explicitly_set(self):
        """9. SaleReturn.customer explicitly set resolves correctly"""
        c = self._create_customer("Customer 9", "+998901110009")
        s = self._create_sale(c, amount=Decimal("300.00"))
        self._create_return(s, amount=Decimal("120.00"), customer=c)

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        row = self._get_customer_row(data, c.id)
        self.assertIsNotNone(row)
        self.assertEqual(row["purchases"], "180.00")  # 300 - 120

    def test_10_multiple_sales_and_multiple_returns_no_cartesian_multiplication(self):
        """
        10. Multiple sales and multiple returns:
            Sale 1: 100, Sale 2: 200, Sale 3: 300 (Total sales: 600)
            Return 1: 50, Return 2: 70 (Total returns: 120)
            Expected purchases: 600 - 120 = 480 (NOT Cartesian 600*2 or 120*3).
        """
        c = self._create_customer("Customer 10", "+998901110010")
        s1 = self._create_sale(c, amount=Decimal("100.00"))
        s2 = self._create_sale(c, amount=Decimal("200.00"))
        s3 = self._create_sale(c, amount=Decimal("300.00"))

        self._create_return(s1, amount=Decimal("50.00"), customer=c)
        self._create_return(s2, amount=Decimal("70.00"), customer=None)

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        row = self._get_customer_row(data, c.id)
        self.assertIsNotNone(row)
        self.assertEqual(row["purchases"], "480.00")

    def test_11_store_isolation(self):
        """11. Store A / Store B isolation"""
        c = self._create_customer("Customer 11", "+998901110011")
        # Store A: sale 400, return 50 -> net 350
        s_a = self._create_sale(c, store=self.store_a, amount=Decimal("400.00"))
        self._create_return(s_a, store=self.store_a, amount=Decimal("50.00"), customer=c)

        # Store B: sale 800, return 100 -> net 700
        s_b = self._create_sale(c, store=self.store_b, amount=Decimal("800.00"))
        self._create_return(s_b, store=self.store_b, amount=Decimal("100.00"), customer=c)

        # Store A Report
        data_a = ReportBuilderService.generate({
            "report_type": "customers",
            "store_id": str(self.store_a.id),
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)
        row_a = self._get_customer_row(data_a, c.id)
        self.assertEqual(row_a["purchases"], "350.00")

        # Store B Report
        data_b = ReportBuilderService.generate({
            "report_type": "customers",
            "store_id": str(self.store_b.id),
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)
        row_b = self._get_customer_row(data_b, c.id)
        self.assertEqual(row_b["purchases"], "700.00")

        # All stores
        data_all = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)
        row_all = self._get_customer_row(data_all, c.id)
        self.assertEqual(row_all["purchases"], "1050.00")

    def test_12_search_filter(self):
        """12. Search filter by name or phone"""
        c1 = self._create_customer("UniqueSherzod", "+998907771122")
        c2 = self._create_customer("DifferentOlim", "+998908883344")

        self._create_sale(c1, amount=Decimal("150.00"))
        self._create_sale(c2, amount=Decimal("250.00"))

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "search": "UniqueSherzod",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        names = [r["name"] for r in data["rows"]]
        self.assertIn("UniqueSherzod", names)
        self.assertNotIn("DifferentOlim", names)

    def test_13_has_debt_filter(self):
        """13. has_debt=1 filters only customers with cumulative debt > 0"""
        c_debt = self._create_customer("Debtor Customer", "+998909990001")
        c_clean = self._create_customer("Clean Customer", "+998909990002")

        CustomerDebt.objects.create(
            customer=c_debt,
            amount=Decimal("500.00"),
            type=CustomerDebt.Type.INCREASE,
        )

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "has_debt": "1",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        names = [r["name"] for r in data["rows"]]
        self.assertIn("Debtor Customer", names)
        self.assertNotIn("Clean Customer", names)

    def test_14_response_structure_preserved(self):
        """14. Response structure and column keys preserved"""
        c = self._create_customer("Customer 14", "+998901110014")
        self._create_sale(c, amount=Decimal("100.00"))
        CustomerDebt.objects.create(
            customer=c,
            amount=Decimal("50.00"),
            type=CustomerDebt.Type.INCREASE,
        )

        data = ReportBuilderService.generate({
            "report_type": "customers",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        # Columns check
        expected_col_keys = {"name", "phone", "purchases", "debt"}
        col_keys = {c["key"] for c in data["columns"]}
        self.assertEqual(col_keys, expected_col_keys)

        # Rows check
        row = self._get_customer_row(data, c.id)
        self.assertEqual(set(row.keys()), expected_col_keys)
        self.assertIsInstance(row["name"], str)
        self.assertIsInstance(row["phone"], str)
        self.assertIsInstance(row["purchases"], str)
        self.assertIsInstance(row["debt"], str)

        # Summary check
        summary_labels = {s["label"] for s in data["summary"]}
        self.assertEqual(summary_labels, {"Mijozlar soni", "Davrdagi xaridlar", "Jami qarzdorlik"})

    def test_15_summary_purchases_matches_filtered_queryset_sum(self):
        """15. Summary purchases strictly matches the filtered customer queryset sum"""
        c1 = self._create_customer("Filterable A", "+998901119991")
        c2 = self._create_customer("Filterable B", "+998901119992")

        # c1 net: 200 - 50 = 150
        s1 = self._create_sale(c1, amount=Decimal("200.00"))
        self._create_return(s1, amount=Decimal("50.00"), customer=c1)

        # c2 net: 300 - 100 = 200
        s2 = self._create_sale(c2, amount=Decimal("300.00"))
        self._create_return(s2, amount=Decimal("100.00"), customer=c2)

        # Search for only c1
        data_filtered = ReportBuilderService.generate({
            "report_type": "customers",
            "search": "Filterable A",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.user)

        summary_map = {s["label"]: s["value"] for s in data_filtered["summary"]}
        self.assertEqual(summary_map["Mijozlar soni"], 1)
        self.assertEqual(summary_map["Davrdagi xaridlar"], "150.00")
