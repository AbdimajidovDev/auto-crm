"""
Regression test suite for PaymentStructureService under Period Transactional Accounting.

Semantic rules:
- Payment.is_refund=False -> +Payment.amount
- Payment.is_refund=True  -> -Payment.amount
- Enters report period according to Payment.created_at (transaction timestamp).
- Respects store filtering via sale__store_id.
- Preserves existing response format and debt_agg.
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase

from apps.debts.models import CustomerDebt
from apps.reports.services.report_service import PaymentStructureService
from apps.sales.models import BankCard, Payment, Sale
from apps.store.models import Store
from apps.users.models.customers import Customer
from apps.users.models.user import User


class PaymentStructurePeriodAccountingTest(TestCase):
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
        cls.customer = Customer.objects.create(
            full_name="Ali Valiyev",
            phone_number="+998901234567",
        )
        cls.bank_card = BankCard.objects.create(
            name="Uzcard",
            is_default=True,
            is_active=True,
            scope=BankCard.Scope.BOTH,
        )

    def _create_sale(self, store, amount=Decimal("1000.00"), status=Sale.Status.PAID, created_at=None, customer=None):
        sale = Sale.objects.create(
            store=store,
            seller=self.user,
            customer=customer,
            total_amount=amount,
            paid_amount=amount if status == Sale.Status.PAID else Decimal("0.00"),
            status=status,
        )
        target_dt = created_at or self.DEFAULT_DT
        Sale.objects.filter(id=sale.id).update(created_at=target_dt)
        sale.refresh_from_db()
        return sale

    def _create_payment(self, sale, p_type, amount, is_refund=False, is_debt_payment=False, created_at=None, bank_card=None):
        if p_type == Payment.Type.CARD and bank_card is None:
            bank_card = self.bank_card
        payment = Payment.objects.create(
            sale=sale,
            type=p_type,
            amount=amount,
            is_refund=is_refund,
            is_debt_payment=is_debt_payment,
            bank_card=bank_card,
        )
        target_dt = created_at or self.DEFAULT_DT
        Payment.objects.filter(id=payment.id).update(created_at=target_dt)
        payment.refresh_from_db()
        return payment

    def _create_debt_record(self, sale, amount, d_type, created_at=None, customer=None):
        debt = CustomerDebt.objects.create(
            customer=customer or sale.customer or self.customer,
            sale=sale,
            amount=amount,
            type=d_type,
        )
        target_dt = created_at or self.DEFAULT_DT
        CustomerDebt.objects.filter(id=debt.id).update(created_at=target_dt)
        debt.refresh_from_db()
        return debt

    def test_1_cash_payment(self):
        """1. Cash payment: +100"""
        sale = self._create_sale(self.store_a)
        self._create_payment(sale, Payment.Type.CASH, Decimal("100.00"), is_refund=False)

        res = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)
        cash_row = next(r for r in res if r["type"] == "cash")

        self.assertEqual(cash_row["method"], "Naqd")
        self.assertEqual(cash_row["amount"], Decimal("100.00"))
        self.assertEqual(cash_row["count"], 1)
        self.assertEqual(cash_row["percent"], "100.0%")

    def test_2_card_payment(self):
        """2. Card payment: +200"""
        sale = self._create_sale(self.store_a)
        self._create_payment(sale, Payment.Type.CARD, Decimal("200.00"), is_refund=False)

        res = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)
        card_row = next(r for r in res if r["type"] == "card")

        self.assertEqual(card_row["method"], "Karta")
        self.assertEqual(card_row["amount"], Decimal("200.00"))
        self.assertEqual(card_row["count"], 1)
        self.assertEqual(card_row["percent"], "100.0%")

    def test_3_cash_refund(self):
        """3. Cash refund: -50"""
        sale = self._create_sale(self.store_a)
        self._create_payment(sale, Payment.Type.CASH, Decimal("50.00"), is_refund=True)

        res = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)
        cash_row = next(r for r in res if r["type"] == "cash")

        self.assertEqual(cash_row["method"], "Naqd")
        self.assertEqual(cash_row["amount"], Decimal("-50.00"))
        self.assertEqual(cash_row["count"], 1)

    def test_4_card_refund(self):
        """4. Card refund: -100"""
        sale = self._create_sale(self.store_a)
        self._create_payment(sale, Payment.Type.CARD, Decimal("100.00"), is_refund=True)

        res = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)
        card_row = next(r for r in res if r["type"] == "card")

        self.assertEqual(card_row["method"], "Karta")
        self.assertEqual(card_row["amount"], Decimal("-100.00"))
        self.assertEqual(card_row["count"], 1)

    def test_5_same_period_payment_and_refund(self):
        """5. Same-period payment + refund: Cash 100 - 50 = +50"""
        sale = self._create_sale(self.store_a)
        self._create_payment(sale, Payment.Type.CASH, Decimal("100.00"), is_refund=False)
        self._create_payment(sale, Payment.Type.CASH, Decimal("50.00"), is_refund=True)

        res = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)
        cash_row = next(r for r in res if r["type"] == "cash")

        self.assertEqual(cash_row["amount"], Decimal("50.00"))
        self.assertEqual(cash_row["count"], 2)

    def test_6_cross_period_payment_and_refund(self):
        """
        6. Cross-period:
           Sep 30 payment +1000
           Oct 1 refund -1000

           September PaymentStructure = +1000
           October PaymentStructure = -1000
        """
        dt_sep = datetime(2026, 9, 30, 15, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 1, 10, 0, tzinfo=dt_timezone.utc)

        sale = self._create_sale(self.store_a, created_at=dt_sep)
        self._create_payment(sale, Payment.Type.CASH, Decimal("1000.00"), is_refund=False, created_at=dt_sep)
        self._create_payment(sale, Payment.Type.CASH, Decimal("1000.00"), is_refund=True, created_at=dt_oct)

        # September Report
        res_sep = PaymentStructureService.get(date(2026, 9, 1), date(2026, 9, 30), store_id=None)
        cash_sep = next(r for r in res_sep if r["type"] == "cash")
        self.assertEqual(cash_sep["amount"], Decimal("1000.00"))
        self.assertEqual(cash_sep["count"], 1)

        # October Report
        res_oct = PaymentStructureService.get(date(2026, 10, 1), date(2026, 10, 31), store_id=None)
        cash_oct = next(r for r in res_oct if r["type"] == "cash")
        self.assertEqual(cash_oct["amount"], Decimal("-1000.00"))
        self.assertEqual(cash_oct["count"], 1)

    def test_7_multiple_payment_types(self):
        """7. Multiple payment types in same period"""
        sale = self._create_sale(self.store_a)
        self._create_payment(sale, Payment.Type.CASH, Decimal("100.00"), is_refund=False)
        self._create_payment(sale, Payment.Type.CASH, Decimal("30.00"), is_refund=True)
        self._create_payment(sale, Payment.Type.CARD, Decimal("200.00"), is_refund=False)
        self._create_payment(sale, Payment.Type.CARD, Decimal("50.00"), is_refund=True)

        res = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)
        rows_by_type = {r["type"]: r for r in res}

        self.assertEqual(rows_by_type["cash"]["amount"], Decimal("70.00"))  # 100 - 30
        self.assertEqual(rows_by_type["cash"]["count"], 2)
        self.assertEqual(rows_by_type["card"]["amount"], Decimal("150.00"))  # 200 - 50
        self.assertEqual(rows_by_type["card"]["count"], 2)

    def test_8_multiple_stores(self):
        """
        8. Multiple stores:
           store A payment faqat A reportida,
           store B payment faqat B reportida.
        """
        sale_a = self._create_sale(self.store_a)
        sale_b = self._create_sale(self.store_b)

        self._create_payment(sale_a, Payment.Type.CASH, Decimal("300.00"), is_refund=False)
        self._create_payment(sale_b, Payment.Type.CASH, Decimal("500.00"), is_refund=False)

        # Store A Report
        res_a = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=self.store_a.id)
        cash_a = next(r for r in res_a if r["type"] == "cash")
        self.assertEqual(cash_a["amount"], Decimal("300.00"))

        # Store B Report
        res_b = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=self.store_b.id)
        cash_b = next(r for r in res_b if r["type"] == "cash")
        self.assertEqual(cash_b["amount"], Decimal("500.00"))

        # All stores
        res_all = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)
        cash_all = next(r for r in res_all if r["type"] == "cash")
        self.assertEqual(cash_all["amount"], Decimal("800.00"))

    def test_9_existing_response_format_preserved_including_debt(self):
        """9. Existing response format o'zgarmasin: keys, types and debt_agg"""
        sale_debt = self._create_sale(self.store_a, amount=Decimal("1000.00"), status=Sale.Status.DEBT)
        sale_cash = self._create_sale(self.store_a)
        self._create_payment(sale_cash, Payment.Type.CASH, Decimal("500.00"), is_refund=False)

        res = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)

        expected_keys = {"method", "type", "count", "amount", "percent"}
        for row in res:
            self.assertEqual(set(row.keys()), expected_keys)
            self.assertIsInstance(row["method"], str)
            self.assertIsInstance(row["type"], str)
            self.assertIsInstance(row["count"], int)
            self.assertIsInstance(row["amount"], Decimal)
            self.assertIsInstance(row["percent"], str)
            self.assertTrue(row["percent"].endswith("%"))

        # debt row check
        debt_row = next((r for r in res if r["type"] == "debt"), None)
        self.assertIsNotNone(debt_row)
        self.assertEqual(debt_row["method"], "Qarz")
        self.assertEqual(debt_row["amount"], Decimal("1000.00"))

    def test_10_same_period_debt_and_payment(self):
        """10. Same-period debt + payment:
        Jan 10: Sale 1000, paid 200 cash, initial debt 800
        Jan 20: Debt payment 300 cash (is_debt_payment=True)
        Jan Report: Cash 500 (50.0%), Debt 500 (50.0%)
        """
        dt_10 = datetime(2026, 1, 10, 10, 0, tzinfo=dt_timezone.utc)
        dt_20 = datetime(2026, 1, 20, 15, 0, tzinfo=dt_timezone.utc)

        sale = self._create_sale(self.store_a, amount=Decimal("1000.00"), status=Sale.Status.PARTIAL, created_at=dt_10, customer=self.customer)
        self._create_payment(sale, Payment.Type.CASH, Decimal("200.00"), is_refund=False, is_debt_payment=False, created_at=dt_10)
        self._create_debt_record(sale, Decimal("800.00"), CustomerDebt.Type.INCREASE, created_at=dt_10)

        # Later in same period: Customer pays 300 cash towards debt
        self._create_payment(sale, Payment.Type.CASH, Decimal("300.00"), is_refund=False, is_debt_payment=True, created_at=dt_20)
        self._create_debt_record(sale, Decimal("300.00"), CustomerDebt.Type.DECREASE, created_at=dt_20)

        res = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)
        rows_by_type = {r["type"]: r for r in res}

        self.assertIn("cash", rows_by_type)
        self.assertIn("debt", rows_by_type)

        self.assertEqual(rows_by_type["cash"]["amount"], Decimal("500.00"))
        self.assertEqual(rows_by_type["cash"]["count"], 2)
        self.assertEqual(rows_by_type["cash"]["percent"], "50.0%")

        self.assertEqual(rows_by_type["debt"]["amount"], Decimal("500.00"))
        self.assertEqual(rows_by_type["debt"]["count"], 1)
        self.assertEqual(rows_by_type["debt"]["percent"], "50.0%")

    def test_11_cross_period_debt_payment_preserves_history(self):
        """11. Cross-period debt payment:
        Sep 15: Sale 1000, paid 200 cash, initial debt 800
        Oct 15: Debt payment 300 cash (is_debt_payment=True)
        Sale status & paid_amount are mutated by payment, but September report MUST preserve historical 800 debt!
        September Report: Cash 200 (20.0%), Debt 800 (80.0%)
        October Report: Cash 300 (100.0%), Debt row absent (0)
        Combined Report: Cash 500 (50.0%), Debt 500 (50.0%)
        """
        dt_sep = datetime(2026, 9, 15, 10, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 15, 14, 0, tzinfo=dt_timezone.utc)

        sale = self._create_sale(self.store_a, amount=Decimal("1000.00"), status=Sale.Status.PARTIAL, created_at=dt_sep, customer=self.customer)
        self._create_payment(sale, Payment.Type.CASH, Decimal("200.00"), is_refund=False, is_debt_payment=False, created_at=dt_sep)
        self._create_debt_record(sale, Decimal("800.00"), CustomerDebt.Type.INCREASE, created_at=dt_sep)

        # Cross-period payment in October
        self._create_payment(sale, Payment.Type.CASH, Decimal("300.00"), is_refund=False, is_debt_payment=True, created_at=dt_oct)
        self._create_debt_record(sale, Decimal("300.00"), CustomerDebt.Type.DECREASE, created_at=dt_oct)

        # Mutate current-state fields as DebtService would do in production
        Sale.objects.filter(id=sale.id).update(paid_amount=Decimal("500.00"), status=Sale.Status.PARTIAL)

        # September Report (Historical period snapshot must NOT be mutated by October payment)
        res_sep = PaymentStructureService.get(date(2026, 9, 1), date(2026, 9, 30), store_id=None)
        sep_rows = {r["type"]: r for r in res_sep}
        self.assertEqual(sep_rows["cash"]["amount"], Decimal("200.00"))
        self.assertEqual(sep_rows["cash"]["percent"], "20.0%")
        self.assertEqual(sep_rows["debt"]["amount"], Decimal("800.00"))
        self.assertEqual(sep_rows["debt"]["percent"], "80.0%")

        # October Report (Payment-only period: Cash 300, Debt 0 -> no debt row)
        res_oct = PaymentStructureService.get(date(2026, 10, 1), date(2026, 10, 31), store_id=None)
        oct_rows = {r["type"]: r for r in res_oct}
        self.assertEqual(oct_rows["cash"]["amount"], Decimal("300.00"))
        self.assertEqual(oct_rows["cash"]["percent"], "100.0%")
        self.assertNotIn("debt", oct_rows)

        # Combined September + October Report
        res_both = PaymentStructureService.get(date(2026, 9, 1), date(2026, 10, 31), store_id=None)
        both_rows = {r["type"]: r for r in res_both}
        self.assertEqual(both_rows["cash"]["amount"], Decimal("500.00"))
        self.assertEqual(both_rows["cash"]["percent"], "50.0%")
        self.assertEqual(both_rows["debt"]["amount"], Decimal("500.00"))
        self.assertEqual(both_rows["debt"]["percent"], "50.0%")

    def test_12_cross_period_return_reducing_debt(self):
        """12. Cross-period return reducing debt:
        Sep 15: Sale 1000 on credit (debt 1000)
        Oct 15: Return of 400 reduces debt (no cash refund)
        September Report: Debt 1000 (100.0%)
        October Report: Debt -400 (100.0%)
        Combined Report: Debt 600 (100.0%)
        """
        dt_sep = datetime(2026, 9, 15, 10, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 15, 14, 0, tzinfo=dt_timezone.utc)

        sale = self._create_sale(self.store_a, amount=Decimal("1000.00"), status=Sale.Status.DEBT, created_at=dt_sep, customer=self.customer)
        self._create_debt_record(sale, Decimal("1000.00"), CustomerDebt.Type.INCREASE, created_at=dt_sep)

        # Return in October reducing debt (CustomerDebt decrease without Payment)
        self._create_debt_record(sale, Decimal("400.00"), CustomerDebt.Type.DECREASE, created_at=dt_oct)

        # September Report
        res_sep = PaymentStructureService.get(date(2026, 9, 1), date(2026, 9, 30), store_id=None)
        sep_debt = next(r for r in res_sep if r["type"] == "debt")
        self.assertEqual(sep_debt["amount"], Decimal("1000.00"))
        self.assertEqual(sep_debt["percent"], "100.0%")

        # October Report (Return-only period for debt)
        res_oct = PaymentStructureService.get(date(2026, 10, 1), date(2026, 10, 31), store_id=None)
        oct_debt = next(r for r in res_oct if r["type"] == "debt")
        self.assertEqual(oct_debt["amount"], Decimal("-400.00"))
        self.assertEqual(oct_debt["percent"], "100.0%")

        # Combined Report
        res_both = PaymentStructureService.get(date(2026, 9, 1), date(2026, 10, 31), store_id=None)
        both_debt = next(r for r in res_both if r["type"] == "debt")
        self.assertEqual(both_debt["amount"], Decimal("600.00"))
        self.assertEqual(both_debt["percent"], "100.0%")

    def test_13_payment_only_period(self):
        """13. Payment-only period:
        Only debt payments occur in the period.
        Report should show 100% Cash, debt row must be omitted (no division by zero).
        """
        dt_sep = datetime(2026, 9, 15, 10, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 15, 14, 0, tzinfo=dt_timezone.utc)

        sale = self._create_sale(self.store_a, amount=Decimal("1000.00"), status=Sale.Status.DEBT, created_at=dt_sep, customer=self.customer)
        self._create_debt_record(sale, Decimal("1000.00"), CustomerDebt.Type.INCREASE, created_at=dt_sep)

        # Debt payment in October
        self._create_payment(sale, Payment.Type.CASH, Decimal("500.00"), is_refund=False, is_debt_payment=True, created_at=dt_oct)
        self._create_debt_record(sale, Decimal("500.00"), CustomerDebt.Type.DECREASE, created_at=dt_oct)

        res_oct = PaymentStructureService.get(date(2026, 10, 1), date(2026, 10, 31), store_id=None)
        self.assertEqual(len(res_oct), 1)
        self.assertEqual(res_oct[0]["type"], "cash")
        self.assertEqual(res_oct[0]["amount"], Decimal("500.00"))
        self.assertEqual(res_oct[0]["percent"], "100.0%")

    def test_14_return_only_period_debt_reduction(self):
        """14. Return-only period with debt reduction:
        No sales or payments in October, only return reducing old debt.
        Report should show debt amount = -300, percent = 100.0%.
        """
        dt_sep = datetime(2026, 9, 15, 10, 0, tzinfo=dt_timezone.utc)
        dt_oct = datetime(2026, 10, 15, 14, 0, tzinfo=dt_timezone.utc)

        sale = self._create_sale(self.store_a, amount=Decimal("1000.00"), status=Sale.Status.DEBT, created_at=dt_sep, customer=self.customer)
        self._create_debt_record(sale, Decimal("1000.00"), CustomerDebt.Type.INCREASE, created_at=dt_sep)

        # Return in October
        self._create_debt_record(sale, Decimal("300.00"), CustomerDebt.Type.DECREASE, created_at=dt_oct)

        res_oct = PaymentStructureService.get(date(2026, 10, 1), date(2026, 10, 31), store_id=None)
        self.assertEqual(len(res_oct), 1)
        self.assertEqual(res_oct[0]["type"], "debt")
        self.assertEqual(res_oct[0]["amount"], Decimal("-300.00"))
        self.assertEqual(res_oct[0]["percent"], "100.0%")
        self.assertEqual(res_oct[0]["count"], 1)

    def test_15_soft_deleted_sale_excluded(self):
        """15. Soft-deleted sale must be excluded from debt calculation."""
        sale = self._create_sale(self.store_a, amount=Decimal("1000.00"), status=Sale.Status.DEBT, customer=self.customer)
        self._create_debt_record(sale, Decimal("1000.00"), CustomerDebt.Type.INCREASE)

        # Soft delete the sale
        Sale.objects.filter(id=sale.id).update(deleted_at=datetime(2026, 1, 16, tzinfo=dt_timezone.utc))

        res = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)
        debt_row = next((r for r in res if r["type"] == "debt"), None)
        self.assertIsNone(debt_row)

    def test_16_multi_store_debt_isolation(self):
        """16. Debt must be strictly scoped to requested store."""
        sale_a = self._create_sale(self.store_a, amount=Decimal("1000.00"), status=Sale.Status.DEBT, customer=self.customer)
        self._create_debt_record(sale_a, Decimal("1000.00"), CustomerDebt.Type.INCREASE)

        sale_b = self._create_sale(self.store_b, amount=Decimal("2000.00"), status=Sale.Status.DEBT, customer=self.customer)
        self._create_debt_record(sale_b, Decimal("2000.00"), CustomerDebt.Type.INCREASE)

        # Store A Report
        res_a = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=self.store_a.id)
        debt_a = next(r for r in res_a if r["type"] == "debt")
        self.assertEqual(debt_a["amount"], Decimal("1000.00"))

        # Store B Report
        res_b = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=self.store_b.id)
        debt_b = next(r for r in res_b if r["type"] == "debt")
        self.assertEqual(debt_b["amount"], Decimal("2000.00"))

        # All Stores Report
        res_all = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)
        debt_all = next(r for r in res_all if r["type"] == "debt")
        self.assertEqual(debt_all["amount"], Decimal("3000.00"))

    def test_17_query_count_performance(self):
        """17. Query count must be O(1) regardless of number of sales, payments, or debts."""
        for i in range(10):
            sale = self._create_sale(self.store_a, amount=Decimal("100.00"), status=Sale.Status.PARTIAL, customer=self.customer)
            self._create_payment(sale, Payment.Type.CASH, Decimal("30.00"), is_refund=False, is_debt_payment=False)
            self._create_debt_record(sale, Decimal("70.00"), CustomerDebt.Type.INCREASE)
            self._create_payment(sale, Payment.Type.CARD, Decimal("20.00"), is_refund=False, is_debt_payment=True)
            self._create_debt_record(sale, Decimal("20.00"), CustomerDebt.Type.DECREASE)

        # 4 queries: 1 Payment qs, 1 Sales agg, 1 CustomerDebt decrease agg, 1 Payment debt payments agg
        with self.assertNumQueries(4):
            res = PaymentStructureService.get(date(2026, 1, 1), date(2026, 1, 31), store_id=None)

        self.assertTrue(len(res) >= 2)

