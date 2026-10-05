"""
Test suite for SaleStatisticsAPIView under Period Transactional Accounting.

Accounting Invariants:
1. Sales stream:
   - Sale.created_at ∈ [start, end), deleted_at IS NULL, store scoping.
   - total_sales = COUNT(Sale.id) (Sale.Status.RETURNED is NOT excluded from sale period).
   - total_amount = SUM(Sale.total_amount).
   - sold_cogs = SUM(SaleItem.quantity * SaleItem.purchase_price).
   - sold_profit = total_amount - sold_cogs.

2. Returns stream:
   - SaleReturn.created_at ∈ [start, end), sale__deleted_at IS NULL, store scoping.
   - total_returned = SUM(SaleReturn.total_refund) (or SaleReturnItem.total_price fallback).
   - return_cogs = SUM(SaleReturnItem.quantity * SaleReturnItem.sale_item.purchase_price) (Historical COGS).
   - return_lost_profit = total_returned - return_cogs.

3. Period Transactional Netting:
   - total_net = total_amount - total_returned.
   - total_profit = sold_profit - return_lost_profit.

4. Cross-Period Return Invariant:
   - Past period sale metrics remain 100% unchanged after future return.
   - Return period reflects return as an independent transaction stream.
   - Return-only period produces correct negative net revenue and lost profit without error.
"""

from datetime import date, datetime, time, timezone as dt_timezone
from decimal import Decimal
import uuid

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.debts.models import CustomerDebt
from apps.products.models import Category, Product, ProductBatch
from apps.sales.models import BankCard, Payment, Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.sales.views.sale_view import SaleStatisticsAPIView
from apps.store.models import Store, StoreUser
from apps.users.models.customers import Customer
from apps.users.models.user import User


class SaleStatisticsPeriodAccountingTest(TestCase):
    SEPT_DT = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
    OCT_DT = datetime(2026, 10, 5, 12, 0, tzinfo=dt_timezone.utc)

    @classmethod
    def setUpTestData(cls):
        cls.store_a = Store.objects.create(name="Store A", phone_number="+998901111111")
        cls.store_b = Store.objects.create(name="Store B", phone_number="+998902222222")

        cls.superuser = User.objects.create(
            phone_number="+998903333333",
            email="admin@example.com",
            is_staff=True,
            is_superuser=True,
        )

        cls.manager_a = User.objects.create(
            phone_number="+998904444444",
            email="manager_a@example.com",
            is_staff=True,
            is_superuser=False,
        )
        StoreUser.objects.create(user=cls.manager_a, store=cls.store_a, is_active=True)

        cls.bank_card = BankCard.objects.create(
            name="Uzcard",
            is_default=True,
            is_active=True,
            scope=BankCard.Scope.BOTH,
        )

        cls.cat = Category.objects.create(name="Ehtiyot qismlar")

        cls.prod_oil = Product.objects.create(
            name="Motor Oil 5W-40",
            sku="OIL-001",
            barcode="666600000001",
            category=cls.cat,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_filter = Product.objects.create(
            name="Oil Filter",
            sku="FLT-001",
            barcode="666600000002",
            category=cls.cat,
            status=Product.ProductStatus.ACTIVE,
        )

        cls.cust_ali = Customer.objects.create(full_name="Ali Valiyev", phone_number="+998901234567")
        cls.factory = APIRequestFactory()

    def _create_sale(
        self,
        store,
        product,
        qty=Decimal("10.00"),
        unit_price=Decimal("100.00"),
        purchase_price=Decimal("60.00"),
        customer=None,
        status=Sale.Status.PAID,
        created_at=None,
        deleted_at=None,
        discount_amount=Decimal("0.00"),
        seller=None,
    ):
        total_items = qty * unit_price
        total_amount = total_items - discount_amount

        sale = Sale.objects.create(
            store=store,
            seller=seller or self.superuser,
            customer=customer or self.cust_ali,
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
            total_price=total_items,
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
        qty=Decimal("2.00"),
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
            seller=self.superuser,
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

    def _get_stats(self, user=None, params=None):
        request = self.factory.get("/api/sales/statistics/", params or {})
        force_authenticate(request, user=user or self.superuser)
        view = SaleStatisticsAPIView.as_view()
        response = view(request)
        return response.data

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Same-period sale and partial return
    # ─────────────────────────────────────────────────────────────────────────
    def test_01_same_period_sale_and_partial_return(self):
        """Sale: 10 units @ 100 (cost 60), rev=1000, cost=600, profit=400.
        Return in same period: 3 units @ 100, ref=300, ret_cost=180, lost_profit=120.
        Expected net: total_sales=1, total_amount=1000.00, total_returned=300.00,
        total_net=700.00, total_profit=280.00 (400 - 120)."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            created_at=self.SEPT_DT,
        )
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("3"),
            unit_price=Decimal("100.00"),
            refund_amount=Decimal("300.00"),
            created_at=self.SEPT_DT,
        )

        data = self._get_stats(params={"date_from": "2026-09-01", "date_to": "2026-09-30"})
        self.assertEqual(data["total_sales"], 1)
        self.assertEqual(data["total_amount"], "1000.00")
        self.assertEqual(data["total_returned"], "300.00")
        self.assertEqual(data["total_net"], "700.00")
        self.assertEqual(data["total_profit"], "280.00")
        self.assertFalse(data["profit_partial"])

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Cross-period full return (September sale, October full return)
    # ─────────────────────────────────────────────────────────────────────────
    def test_02_cross_period_full_return_september_october(self):
        """Sale in September: 10 units @ 100 (cost 60), total 1000, profit 400.
        Full return in October: 10 units @ 100, total 1000, lost profit 400.
        Sale status becomes RETURNED.

        Invariants:
        - September report must NOT change after October return (remains rev 1000, profit 400).
        - Sale.Status.RETURNED must NOT be excluded from September.
        - October report reflects return: total_sales=0, total_amount=0.00,
          total_returned=1000.00, total_net=-1000.00, total_profit=-400.00."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            created_at=self.SEPT_DT,
        )
        # Full return in October
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            refund_amount=Decimal("1000.00"),
            created_at=self.OCT_DT,
        )
        # Mark sale as RETURNED (current lifecycle state)
        Sale.objects.filter(id=sale.id).update(status=Sale.Status.RETURNED)

        # 1. September query (historical integrity)
        sep_data = self._get_stats(params={"date_from": "2026-09-01", "date_to": "2026-09-30"})
        self.assertEqual(sep_data["total_sales"], 1)
        self.assertEqual(sep_data["total_amount"], "1000.00")
        self.assertEqual(sep_data["total_returned"], "0.00")
        self.assertEqual(sep_data["total_net"], "1000.00")
        self.assertEqual(sep_data["total_profit"], "400.00")

        # 2. October query (return period)
        oct_data = self._get_stats(params={"date_from": "2026-10-01", "date_to": "2026-10-31"})
        self.assertEqual(oct_data["total_sales"], 0)
        self.assertEqual(oct_data["total_amount"], "0.00")
        self.assertEqual(oct_data["total_returned"], "1000.00")
        self.assertEqual(oct_data["total_net"], "-1000.00")
        self.assertEqual(oct_data["total_profit"], "-400.00")

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Cross-period partial return (September sale, October partial return)
    # ─────────────────────────────────────────────────────────────────────────
    def test_03_cross_period_partial_return_september_october(self):
        """Sale in September: 10 units @ 100 (cost 60), total 1000, profit 400.
        Partial return in October: 2 units @ 100, ref 200, ret_cost 120, lost profit 80."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            created_at=self.SEPT_DT,
        )
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("2"),
            unit_price=Decimal("100.00"),
            refund_amount=Decimal("200.00"),
            created_at=self.OCT_DT,
        )

        # September remains unaffected
        sep = self._get_stats(params={"date_from": "2026-09-01", "date_to": "2026-09-30"})
        self.assertEqual(sep["total_sales"], 1)
        self.assertEqual(sep["total_amount"], "1000.00")
        self.assertEqual(sep["total_net"], "1000.00")
        self.assertEqual(sep["total_profit"], "400.00")
        self.assertEqual(sep["total_returned"], "0.00")

        # October shows partial return deduction
        oct_res = self._get_stats(params={"date_from": "2026-10-01", "date_to": "2026-10-31"})
        self.assertEqual(oct_res["total_sales"], 0)
        self.assertEqual(oct_res["total_amount"], "0.00")
        self.assertEqual(oct_res["total_returned"], "200.00")
        self.assertEqual(oct_res["total_net"], "-200.00")
        self.assertEqual(oct_res["total_profit"], "-80.00")

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Return-only period (No sales at all in query period)
    # ─────────────────────────────────────────────────────────────────────────
    def test_04_return_only_period(self):
        """Period with 0 sales and 1 return should succeed without DivisionByZero or None errors."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("50.00"),
            purchase_price=Decimal("30.00"),
            created_at=self.SEPT_DT,
        )
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("1"),
            unit_price=Decimal("50.00"),
            refund_amount=Decimal("50.00"),
            created_at=self.OCT_DT,
        )

        data = self._get_stats(params={"date_from": "2026-10-01", "date_to": "2026-10-31"})
        self.assertEqual(data["total_sales"], 0)
        self.assertEqual(data["total_amount"], "0.00")
        self.assertEqual(data["total_returned"], "50.00")
        self.assertEqual(data["total_net"], "-50.00")
        # Lost profit = 50 - 30 = 20 -> total_profit = -20.00
        self.assertEqual(data["total_profit"], "-20.00")

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Soft-delete exclusion
    # ─────────────────────────────────────────────────────────────────────────
    def test_05_soft_delete_parent_sale_and_returns_excluded(self):
        """Soft-deleted sales and their returns must be excluded from statistics."""
        deleted_dt = datetime(2026, 9, 20, 10, 0, tzinfo=dt_timezone.utc)
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            created_at=self.SEPT_DT,
            deleted_at=deleted_dt,
        )
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("2"),
            refund_amount=Decimal("200.00"),
            created_at=self.OCT_DT,
        )

        # September has 0 active sales
        sep = self._get_stats(params={"date_from": "2026-09-01", "date_to": "2026-09-30"})
        self.assertEqual(sep["total_sales"], 0)
        self.assertEqual(sep["total_amount"], "0.00")
        self.assertEqual(sep["total_profit"], "0.00")

        # October return belonging to soft-deleted sale is excluded
        oct_res = self._get_stats(params={"date_from": "2026-10-01", "date_to": "2026-10-31"})
        self.assertEqual(oct_res["total_returned"], "0.00")
        self.assertEqual(oct_res["total_profit"], "0.00")

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Store isolation and scoping
    # ─────────────────────────────────────────────────────────────────────────
    def test_06_store_isolation_and_scoping(self):
        """Transactions in Store A and Store B must be isolated by store parameter
        and user store assignments."""
        # Store A sale & return
        sale_a, item_a = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            created_at=self.SEPT_DT,
        )
        self._create_return(
            sale=sale_a,
            item=item_a,
            qty=Decimal("1"),
            refund_amount=Decimal("100.00"),
            created_at=self.SEPT_DT,
            store=self.store_a,
        )

        # Store B sale
        self._create_sale(
            store=self.store_b,
            product=self.prod_filter,
            qty=Decimal("5"),
            unit_price=Decimal("200.00"),
            purchase_price=Decimal("120.00"),
            created_at=self.SEPT_DT,
        )

        # Filter by store_a specifically
        data_a = self._get_stats(
            user=self.superuser,
            params={"store": self.store_a.id, "date_from": "2026-09-01", "date_to": "2026-09-30"},
        )
        self.assertEqual(data_a["total_sales"], 1)
        self.assertEqual(data_a["total_amount"], "1000.00")
        self.assertEqual(data_a["total_returned"], "100.00")
        self.assertEqual(data_a["total_net"], "900.00")
        # Profit: (1000 - 600) - (100 - 60) = 400 - 40 = 360.00
        self.assertEqual(data_a["total_profit"], "360.00")

        # Manager A restricted to store A (even without ?store= param) sees only Store A
        data_mgr = self._get_stats(
            user=self.manager_a,
            params={"date_from": "2026-09-01", "date_to": "2026-09-30"},
        )
        self.assertEqual(data_mgr["total_sales"], 1)
        self.assertEqual(data_mgr["total_amount"], "1000.00")
        self.assertEqual(data_mgr["total_profit"], "360.00")

    # ─────────────────────────────────────────────────────────────────────────
    # 7. Historical COGS via SaleReturnItem.sale_item.purchase_price
    # ─────────────────────────────────────────────────────────────────────────
    def test_07_historical_cogs_via_sale_return_item_sale_item_purchase_price(self):
        """Even if current catalog batch price changes, the return must use historical
        purchase_price from the original sale item."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("50.00"),  # Historical cost was 50
            created_at=self.SEPT_DT,
        )
        # Create or update ProductBatch with new price
        ProductBatch.objects.create(
            store=self.store_a,
            product=self.prod_oil,
            quantity=Decimal("50"),
            purchase_price=Decimal("90.00"),  # New cost 90
            selling_price=Decimal("120.00"),
            is_active=True,
        )

        # Return 2 units in October
        self._create_return(
            sale=sale,
            item=item,
            qty=Decimal("2"),
            unit_price=Decimal("100.00"),
            refund_amount=Decimal("200.00"),
            created_at=self.OCT_DT,
        )

        oct_res = self._get_stats(params={"date_from": "2026-10-01", "date_to": "2026-10-31"})
        # Refund = 200. Historical cost = 2 * 50 = 100. Lost profit = 200 - 100 = 100.
        # If it used 90, lost profit would be 200 - 180 = 20 (WRONG).
        self.assertEqual(oct_res["total_profit"], "-100.00")

    # ─────────────────────────────────────────────────────────────────────────
    # 8. Debt and Payment semantics
    # ─────────────────────────────────────────────────────────────────────────
    def test_08_debt_and_payment_semantics(self):
        """Verify CustomerDebt (type='i' and 'd'), Payment (is_refund, is_debt_payment),
        paid_breakdown, returned_breakdown, and recent_debt_payments."""
        # Sale on credit: total 1000, paid 200 cash, debt 800
        sale = Sale.objects.create(
            store=self.store_a,
            seller=self.superuser,
            customer=self.cust_ali,
            total_amount=Decimal("1000.00"),
            paid_amount=Decimal("200.00"),
            status=Sale.Status.PARTIAL,
            payment_type=Sale.PaymentType.MIXED,
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.prod_oil,
            quantity=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            total_price=Decimal("1000.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=self.SEPT_DT)

        # Cash payment of 200 at sale time
        Payment.objects.create(
            sale=sale,
            type=Payment.Type.CASH,
            amount=Decimal("200.00"),
            is_refund=False,
            is_debt_payment=False,
        )
        # Debt increase record: 800
        CustomerDebt.objects.create(
            customer=self.cust_ali,
            sale=sale,
            amount=Decimal("800.00"),
            type=CustomerDebt.Type.INCREASE,
        )

        # Later: Customer pays 300 of debt via Card
        pay_grp = uuid.uuid4()
        Payment.objects.create(
            sale=sale,
            type=Payment.Type.CARD,
            bank_card=self.bank_card,
            amount=Decimal("300.00"),
            is_refund=False,
            is_debt_payment=True,
            payment_group=pay_grp,
        )
        CustomerDebt.objects.create(
            customer=self.cust_ali,
            sale=sale,
            amount=Decimal("300.00"),
            type=CustomerDebt.Type.DECREASE,
        )

        data = self._get_stats(params={"date_from": "2026-09-01", "date_to": "2026-09-30"})
        self.assertEqual(data["total_sales"], 1)
        self.assertEqual(data["total_amount"], "1000.00")
        # Remaining debt: 800 - 300 = 500.00
        self.assertEqual(data["total_debt"], "500.00")
        # Total paid net: 200 cash + 300 card = 500.00
        self.assertEqual(data["total_paid"], "500.00")
        self.assertEqual(len(data["paid_breakdown"]), 2)
        self.assertTrue(any(b["type"] == "cash" and b["amount"] == "200.00" for b in data["paid_breakdown"]))
        self.assertTrue(any(b["type"] == "card" and b["amount"] == "300.00" for b in data["paid_breakdown"]))
        self.assertTrue(len(data["recent_debt_payments"]) >= 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 9. Profit partial flag
    # ─────────────────────────────────────────────────────────────────────────
    def test_09_profit_partial_warning(self):
        """When purchase_price is NULL or 0, profit_partial must be True."""
        sale, item = self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("10"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("0.00"),  # Missing purchase price
            created_at=self.SEPT_DT,
        )
        data = self._get_stats(params={"date_from": "2026-09-01", "date_to": "2026-09-30"})
        self.assertTrue(data["profit_partial"])

    # ─────────────────────────────────────────────────────────────────────────
    # 10. API response contract validation
    # ─────────────────────────────────────────────────────────────────────────
    def test_10_api_response_contract_and_types(self):
        """Verify API response contains exact expected keys and types."""
        self._create_sale(
            store=self.store_a,
            product=self.prod_oil,
            qty=Decimal("5"),
            unit_price=Decimal("100.00"),
            purchase_price=Decimal("60.00"),
            created_at=self.SEPT_DT,
        )
        data = self._get_stats(params={"date_from": "2026-09-01", "date_to": "2026-09-30"})

        expected_keys = {
            "total_sales",
            "total_amount",
            "total_net",
            "total_paid",
            "total_debt",
            "total_profit",
            "profit_partial",
            "total_returned",
            "total_returned_all",
            "paid_breakdown",
            "returned_breakdown",
            "recent_debt_payments",
        }
        self.assertEqual(set(data.keys()), expected_keys)
        self.assertIsInstance(data["total_sales"], int)
        self.assertIsInstance(data["total_amount"], str)
        self.assertIsInstance(data["total_net"], str)
        self.assertIsInstance(data["total_paid"], str)
        self.assertIsInstance(data["total_debt"], str)
        self.assertIsInstance(data["total_profit"], str)
        self.assertIsInstance(data["profit_partial"], bool)
        self.assertIsInstance(data["total_returned"], str)
        self.assertIsInstance(data["total_returned_all"], str)
        self.assertIsInstance(data["paid_breakdown"], list)
        self.assertIsInstance(data["returned_breakdown"], list)
        self.assertIsInstance(data["recent_debt_payments"], list)

    # ─────────────────────────────────────────────────────────────────────────
    # 11. Query performance (O(1) query count, NO N+1)
    # ─────────────────────────────────────────────────────────────────────────
    def test_11_query_performance_no_n_plus_one(self):
        """Verify query count is constant and strictly bounded with 20 sales and 10 returns."""
        sales = []
        for i in range(20):
            s, it = self._create_sale(
                store=self.store_a,
                product=self.prod_oil if i % 2 == 0 else self.prod_filter,
                qty=Decimal("2"),
                unit_price=Decimal("100.00"),
                purchase_price=Decimal("60.00"),
                created_at=self.SEPT_DT,
            )
            sales.append((s, it))

        for i in range(10):
            s, it = sales[i]
            self._create_return(
                sale=s,
                item=it,
                qty=Decimal("1"),
                refund_amount=Decimal("100.00"),
                created_at=self.SEPT_DT,
            )

        # Bounded query count (should be <= 12 queries, strictly no N+1)
        with self.assertNumQueries(11):
            data = self._get_stats(params={"date_from": "2026-09-01", "date_to": "2026-09-30"})
            self.assertEqual(data["total_sales"], 20)
            self.assertEqual(data["total_amount"], "4000.00")
            self.assertEqual(data["total_returned"], "1000.00")
            self.assertEqual(data["total_net"], "3000.00")
            # Gross profit: 4000 - 2400 = 1600. Return lost profit: 1000 - 600 = 400. Net profit: 1200.00
            self.assertEqual(data["total_profit"], "1200.00")
