"""
Test suite for Supplier Returns under Period Transactional Accounting.

Target Services:
1. ReportBuilderService._build_suppliers (apps/reports/services/report_builder.py)
2. SupplierStatisticsService / SupplierReportService / DebtService.supplier_debts (apps/reports/services/report_service.py)

Key Principles:
- Supplier purchase/debt: recognized in purchase transaction period.
- Supplier return (SupplierTransaction.RETURN): recognized in return period (created_at in [start, end)).
- Cross-period returns MUST NOT retrospectively alter prior purchase periods.
- Single source of truth: SupplierTransaction.RETURN without double counting.
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase

from apps.contract.models import StockEntry, StockEntryItem, Supplier, SupplierTransaction
from apps.products.models import Category, Product
from apps.reports.services.report_builder import _build_suppliers
from apps.reports.services.report_service import (
    DebtService,
    SupplierReportService,
    SupplierStatisticsService,
)
from apps.store.models import Store
from apps.users.models.user import User


class SupplierReturnsPeriodAccountingTest(TestCase):
    SEPT_DT = datetime(2026, 9, 15, 10, 0, tzinfo=dt_timezone.utc)
    OCT_DT = datetime(2026, 10, 15, 10, 0, tzinfo=dt_timezone.utc)
    NOV_DT = datetime(2026, 11, 15, 10, 0, tzinfo=dt_timezone.utc)

    SEPT_FROM = "2026-09-01"
    SEPT_TO = "2026-09-30"
    OCT_FROM = "2026-10-01"
    OCT_TO = "2026-10-31"

    @classmethod
    def setUpTestData(cls):
        cls.store_a = Store.objects.create(name="Store A", phone_number="+998901111111")
        cls.store_b = Store.objects.create(name="Store B", phone_number="+998902222222")
        cls.user = User.objects.create(
            phone_number="+998903333333",
            email="manager@example.com",
            is_staff=True,
            is_superuser=True,
        )
        cls.category = Category.objects.create(name="Spare Parts")
        cls.product_1 = Product.objects.create(
            name="Brake Pad",
            category=cls.category,
        )
        cls.product_2 = Product.objects.create(
            name="Oil Filter",
            category=cls.category,
        )
        cls.supplier_1 = Supplier.objects.create(
            name="Supplier Alpha",
            phone_number="+998901234567",
            is_active=True,
        )
        cls.supplier_2 = Supplier.objects.create(
            name="Supplier Beta",
            phone_number="+998907654321",
            is_active=True,
        )
        cls.supplier_inactive = Supplier.objects.create(
            name="Supplier Gamma Inactive",
            phone_number="+998909999999",
            is_active=False,
        )

    def _create_entry(self, supplier, store, total_amount, paid_amount=Decimal("0"), created_at=None):
        debt_amount = total_amount - paid_amount
        entry = StockEntry.objects.create(
            supplier=supplier,
            store=store,
            total_amount=total_amount,
            paid_amount=paid_amount,
            debt_amount=debt_amount,
            created_by=self.user,
        )
        target_dt = created_at or self.SEPT_DT
        StockEntry.objects.filter(id=entry.id).update(created_at=target_dt)
        entry.refresh_from_db()
        return entry

    def _create_entry_item(self, entry, product, quantity=10, price=Decimal("100.00")):
        return StockEntryItem.objects.create(
            entry=entry,
            product=product,
            quantity=Decimal(str(quantity)),
            purchase_price=price,
            selling_price=price * Decimal("1.5"),
        )

    def _create_txn(self, supplier, entry, amount, txn_type, created_at=None):
        txn = SupplierTransaction.objects.create(
            supplier=supplier,
            entry=entry,
            amount=Decimal(str(amount)),
            type=txn_type,
        )
        target_dt = created_at or self.SEPT_DT
        SupplierTransaction.objects.filter(id=txn.id).update(created_at=target_dt)
        txn.refresh_from_db()
        return txn

    # 1. Purchase
    def test_01_purchase(self):
        """1. Purchase: adds to period_in and cumulative debt."""
        entry = self._create_entry(self.supplier_1, self.store_a, Decimal("1000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("1000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)

        cols, qs, row_fn, summary = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)
        row = next(row_fn(s) for s in qs if s.id == self.supplier_1.id)

        self.assertEqual(row["period_in"], "1000.00")
        self.assertEqual(row["period_paid"], "0.00")
        self.assertEqual(row["debt"], "1000.00")

        stat = SupplierStatisticsService.get(date(2026, 9, 1), date(2026, 9, 30), store_id=None)
        self.assertEqual(stat["totalDebt"], Decimal("1000.00"))

    # 2. Payment
    def test_02_payment(self):
        """2. Payment: reduces debt and records period_paid."""
        entry = self._create_entry(self.supplier_1, self.store_a, Decimal("1000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("1000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("300.00"), SupplierTransaction.TransactionType.PAYMENT, created_at=self.SEPT_DT)

        cols, qs, row_fn, summary = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)
        row = next(row_fn(s) for s in qs if s.id == self.supplier_1.id)

        self.assertEqual(row["period_in"], "1000.00")
        self.assertEqual(row["period_paid"], "300.00")
        self.assertEqual(row["debt"], "700.00")

        debts = DebtService.supplier_debts()
        d_row = next(d for d in debts if d["supplierName"] == self.supplier_1.name)
        self.assertEqual(d_row["debt"], Decimal("700.00"))

    # 3. Return
    def test_03_return(self):
        """3. Return: reduces net period_in and cancels debt."""
        entry = self._create_entry(self.supplier_1, self.store_a, Decimal("1000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("1000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("400.00"), SupplierTransaction.TransactionType.RETURN, created_at=self.SEPT_DT)

        cols, qs, row_fn, summary = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)
        row = next(row_fn(s) for s in qs if s.id == self.supplier_1.id)

        self.assertEqual(row["period_in"], "600.00")
        self.assertEqual(row["period_paid"], "0.00")
        self.assertEqual(row["debt"], "600.00")

    # 4. Same-period purchase + return
    def test_04_same_period_purchase_and_return(self):
        """4. Same-period purchase + return netting."""
        entry = self._create_entry(self.supplier_1, self.store_a, Decimal("1000000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("1000000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("200000.00"), SupplierTransaction.TransactionType.RETURN, created_at=self.SEPT_DT)

        cols, qs, row_fn, summary = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)
        row = next(row_fn(s) for s in qs if s.id == self.supplier_1.id)

        self.assertEqual(row["period_in"], "800000.00")
        self.assertEqual(row["debt"], "800000.00")

        stat = SupplierStatisticsService.get(date(2026, 9, 1), date(2026, 9, 30), store_id=None)
        self.assertEqual(stat["totalDebt"], Decimal("800000.00"))

    # 5. Cross-period purchase + return (September purchase, October return)
    def test_05_cross_period_purchase_and_return(self):
        """
        5. Cross-period purchase + return:
        September purchase = 1,000,000 -> Sept report = +1,000,000
        October return = 300,000 -> Oct report = -300,000
        September report must NOT retrospectively mutate!
        """
        entry = self._create_entry(self.supplier_1, self.store_a, Decimal("1000000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("1000000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("300000.00"), SupplierTransaction.TransactionType.RETURN, created_at=self.OCT_DT)

        # Check September report
        cols_sept, qs_sept, row_fn_sept, summary_sept = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)
        row_sept = next(row_fn_sept(s) for s in qs_sept if s.id == self.supplier_1.id)
        self.assertEqual(row_sept["period_in"], "1000000.00")
        self.assertEqual(row_sept["period_paid"], "0.00")

        stat_sept = SupplierStatisticsService.get(date(2026, 9, 1), date(2026, 9, 30), store_id=None)
        self.assertEqual(stat_sept["totalDebt"], Decimal("1000000.00"))

        # Check October report
        cols_oct, qs_oct, row_fn_oct, summary_oct = _build_suppliers({"from": self.OCT_FROM, "to": self.OCT_TO}, store_id=None)
        row_oct = next(row_fn_oct(s) for s in qs_oct if s.id == self.supplier_1.id)
        self.assertEqual(row_oct["period_in"], "-300000.00")
        self.assertEqual(row_oct["period_paid"], "0.00")
        self.assertEqual(row_oct["debt"], "700000.00")  # Cumulative debt remaining

        stat_oct = SupplierStatisticsService.get(date(2026, 10, 1), date(2026, 10, 31), store_id=None)
        self.assertEqual(stat_oct["totalDebt"], Decimal("-300000.00"))

        # Re-check September report: must remain strictly unchanged (+1,000,000.00)
        cols_sept_re, qs_sept_re, row_fn_sept_re, _ = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)
        row_sept_re = next(row_fn_sept_re(s) for s in qs_sept_re if s.id == self.supplier_1.id)
        self.assertEqual(row_sept_re["period_in"], "1000000.00")

    # 6. Cross-period payment
    def test_06_cross_period_payment(self):
        """6. Cross-period payment: payment in October does not affect September report."""
        entry = self._create_entry(self.supplier_1, self.store_a, Decimal("1000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("1000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("400.00"), SupplierTransaction.TransactionType.PAYMENT, created_at=self.OCT_DT)

        # September
        _, qs_sept, row_fn_sept, _ = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)
        row_sept = next(row_fn_sept(s) for s in qs_sept if s.id == self.supplier_1.id)
        self.assertEqual(row_sept["period_in"], "1000.00")
        self.assertEqual(row_sept["period_paid"], "0.00")

        # October
        _, qs_oct, row_fn_oct, _ = _build_suppliers({"from": self.OCT_FROM, "to": self.OCT_TO}, store_id=None)
        row_oct = next(row_fn_oct(s) for s in qs_oct if s.id == self.supplier_1.id)
        self.assertEqual(row_oct["period_in"], "0.00")
        self.assertEqual(row_oct["period_paid"], "400.00")

    # 7. Return-only period
    def test_07_return_only_period(self):
        """7. Return-only period: no purchases or payments, only return."""
        entry = self._create_entry(self.supplier_1, self.store_a, Decimal("500.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("500.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("500.00"), SupplierTransaction.TransactionType.RETURN, created_at=self.OCT_DT)

        _, qs_oct, row_fn_oct, _ = _build_suppliers({"from": self.OCT_FROM, "to": self.OCT_TO}, store_id=None)
        row_oct = next(row_fn_oct(s) for s in qs_oct if s.id == self.supplier_1.id)
        self.assertEqual(row_oct["period_in"], "-500.00")
        self.assertEqual(row_oct["period_paid"], "0.00")
        self.assertEqual(row_oct["debt"], "0.00")

        stat = SupplierStatisticsService.get(date(2026, 10, 1), date(2026, 10, 31), store_id=None)
        self.assertEqual(stat["totalDebt"], Decimal("-500.00"))

    # 8. Payment-only period
    def test_08_payment_only_period(self):
        """8. Payment-only period: no purchases, only payment."""
        entry = self._create_entry(self.supplier_1, self.store_a, Decimal("800.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("800.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, entry, Decimal("350.00"), SupplierTransaction.TransactionType.PAYMENT, created_at=self.OCT_DT)

        _, qs_oct, row_fn_oct, _ = _build_suppliers({"from": self.OCT_FROM, "to": self.OCT_TO}, store_id=None)
        row_oct = next(row_fn_oct(s) for s in qs_oct if s.id == self.supplier_1.id)
        self.assertEqual(row_oct["period_in"], "0.00")
        self.assertEqual(row_oct["period_paid"], "350.00")

        stat = SupplierStatisticsService.get(date(2026, 10, 1), date(2026, 10, 31), store_id=None)
        self.assertEqual(stat["totalDebt"], Decimal("-350.00"))

    # 9. Multiple suppliers
    def test_09_multiple_suppliers(self):
        """9. Multiple suppliers with independent purchases, returns, and payments."""
        # Supplier 1: in 1000, ret 200, pay 300 -> net in 800, paid 300, debt 500
        e1 = self._create_entry(self.supplier_1, self.store_a, Decimal("1000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e1, Decimal("1000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e1, Decimal("200.00"), SupplierTransaction.TransactionType.RETURN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e1, Decimal("300.00"), SupplierTransaction.TransactionType.PAYMENT, created_at=self.SEPT_DT)

        # Supplier 2: in 2000, ret 500, pay 1000 -> net in 1500, paid 1000, debt 500
        e2 = self._create_entry(self.supplier_2, self.store_a, Decimal("2000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_2, e2, Decimal("2000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_2, e2, Decimal("500.00"), SupplierTransaction.TransactionType.RETURN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_2, e2, Decimal("1000.00"), SupplierTransaction.TransactionType.PAYMENT, created_at=self.SEPT_DT)

        cols, qs, row_fn, summary = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)
        row1 = next(row_fn(s) for s in qs if s.id == self.supplier_1.id)
        row2 = next(row_fn(s) for s in qs if s.id == self.supplier_2.id)

        self.assertEqual(row1["period_in"], "800.00")
        self.assertEqual(row1["period_paid"], "300.00")
        self.assertEqual(row1["debt"], "500.00")

        self.assertEqual(row2["period_in"], "1500.00")
        self.assertEqual(row2["period_paid"], "1000.00")
        self.assertEqual(row2["debt"], "500.00")

        sum_map = {item["label"]: item["value"] for item in summary}
        self.assertEqual(sum_map["Davrdagi kirim"], "2300.00")
        self.assertEqual(sum_map["Davrdagi to'lovlar"], "1300.00")

    # 10. Multiple stores
    def test_10_multiple_stores(self):
        """10. Store isolation: transactions filtered by entry__store_id."""
        # Store A: in 1000, ret 300 -> net 700
        e_a = self._create_entry(self.supplier_1, self.store_a, Decimal("1000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e_a, Decimal("1000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e_a, Decimal("300.00"), SupplierTransaction.TransactionType.RETURN, created_at=self.SEPT_DT)

        # Store B: in 2000, ret 400 -> net 1600
        e_b = self._create_entry(self.supplier_1, self.store_b, Decimal("2000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e_b, Decimal("2000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e_b, Decimal("400.00"), SupplierTransaction.TransactionType.RETURN, created_at=self.SEPT_DT)

        # Store A filter
        _, qs_a, row_fn_a, _ = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=self.store_a.id)
        row_a = next(row_fn_a(s) for s in qs_a if s.id == self.supplier_1.id)
        self.assertEqual(row_a["period_in"], "700.00")

        # Store B filter
        _, qs_b, row_fn_b, _ = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=self.store_b.id)
        row_b = next(row_fn_b(s) for s in qs_b if s.id == self.supplier_1.id)
        self.assertEqual(row_b["period_in"], "1600.00")

    # 11. Inactive supplier handling
    def test_11_inactive_supplier_handling(self):
        """11. Inactive supplier: excluded from _build_suppliers."""
        e = self._create_entry(self.supplier_inactive, self.store_a, Decimal("1000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_inactive, e, Decimal("1000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)

        _, qs, _, _ = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)
        self.assertFalse(qs.filter(id=self.supplier_inactive.id).exists())

    # 12. Supplier filter
    def test_12_supplier_filter(self):
        """12. Supplier filter: params={"supplier_id": id} filters to single supplier."""
        e1 = self._create_entry(self.supplier_1, self.store_a, Decimal("1000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e1, Decimal("1000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)

        e2 = self._create_entry(self.supplier_2, self.store_a, Decimal("2000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_2, e2, Decimal("2000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)

        _, qs, _, _ = _build_suppliers(
            {"from": self.SEPT_FROM, "to": self.SEPT_TO, "supplier_id": self.supplier_1.id},
            store_id=None,
        )
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().id, self.supplier_1.id)

    # 13. Consolidated report (store_id=None)
    def test_13_consolidated_report(self):
        """13. Consolidated report: store_id=None sums across all stores."""
        e_a = self._create_entry(self.supplier_1, self.store_a, Decimal("1000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e_a, Decimal("1000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e_a, Decimal("200.00"), SupplierTransaction.TransactionType.RETURN, created_at=self.SEPT_DT)

        e_b = self._create_entry(self.supplier_1, self.store_b, Decimal("2000.00"), created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e_b, Decimal("2000.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
        self._create_txn(self.supplier_1, e_b, Decimal("300.00"), SupplierTransaction.TransactionType.RETURN, created_at=self.SEPT_DT)

        _, qs, row_fn, _ = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)
        row = next(row_fn(s) for s in qs if s.id == self.supplier_1.id)
        # Gross in = 3000, returns = 500 -> net period_in = 2500
        self.assertEqual(row["period_in"], "2500.00")

    # 14. API response contract
    def test_14_api_response_contract(self):
        """14. API response contract: exact column schema, row keys, and summary structure."""
        cols, qs, row_fn, summary = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)

        expected_col_keys = ["name", "phone", "period_in", "period_paid", "debt"]
        self.assertEqual([c["key"] for c in cols], expected_col_keys)

        sample = qs.first()
        if sample:
            r = row_fn(sample)
            self.assertEqual(list(r.keys()), expected_col_keys)

        expected_sum_labels = ["Ta'minotchilar", "Davrdagi kirim", "Davrdagi to'lovlar"]
        self.assertEqual([s["label"] for s in summary], expected_sum_labels)

        # SupplierStatisticsService contract
        stat = SupplierStatisticsService.get(date(2026, 9, 1), date(2026, 9, 30), store_id=None)
        expected_stat_keys = {"supplierCount", "distinctProductCount", "totalPurchaseAmount", "totalDebt"}
        self.assertEqual(set(stat.keys()), expected_stat_keys)

        # SupplierReportService facade
        facade_stat = SupplierReportService.get(date(2026, 9, 1), date(2026, 9, 30), store_id=None)
        self.assertEqual(set(facade_stat.keys()), expected_stat_keys)

        debts = SupplierReportService.debts()
        self.assertIsInstance(debts, list)

    # 15. Query count / performance (O(1) queries)
    def test_15_query_count_performance(self):
        """15. Performance: _build_suppliers and SupplierStatisticsService execute in O(1) queries."""
        # Create multiple entries and transactions across suppliers
        for i in range(5):
            e = self._create_entry(self.supplier_1, self.store_a, Decimal("100.00"), created_at=self.SEPT_DT)
            self._create_txn(self.supplier_1, e, Decimal("100.00"), SupplierTransaction.TransactionType.INVENTORY_IN, created_at=self.SEPT_DT)
            self._create_txn(self.supplier_1, e, Decimal("20.00"), SupplierTransaction.TransactionType.RETURN, created_at=self.SEPT_DT)

        # _build_suppliers: 1 aggregate query + 1 evaluation query (no N+1 per row)
        with self.assertNumQueries(1):
            cols, qs, row_fn, summary = _build_suppliers({"from": self.SEPT_FROM, "to": self.SEPT_TO}, store_id=None)
            # summary triggers the aggregate query

        # Evaluation query for queryset
        with self.assertNumQueries(1):
            rows = [row_fn(s) for s in qs]
            self.assertTrue(len(rows) > 0)

        # SupplierStatisticsService.get executes exactly 3 queries:
        # 1. StockEntry aggregate
        # 2. StockEntryItem aggregate
        # 3. SupplierTransaction aggregate
        with self.assertNumQueries(3):
            stat = SupplierStatisticsService.get(date(2026, 9, 1), date(2026, 9, 30), store_id=None)
            self.assertIsNotNone(stat)
