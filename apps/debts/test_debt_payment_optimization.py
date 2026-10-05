"""
P1 #5 — Customer Debt Payment N+1 Optimization & Correctness Test Suite.

Tests cover:
1. Single debt sale payment (exact & partial).
2. Multiple debt sales FIFO allocation (oldest debt paid first).
3. Large scale debt payment (100+ sales) verifying O(1) query complexity.
4. Partial payment across multiple sales.
5. Exact payment of total debt.
6. Underpayment / partial balance remaining.
7. Overpayment validation rejection.
8. Prior partial debt payments on sales.
9. Returned sale with debt reduction.
10. Soft-deleted sales exclusion.
11. Same-period return against debt and subsequent debt payment.
12. Initial sale payment + later debt payment (payment_type transition check).
13. Zero debt customer validation rejection.
14. Split payment with cash + multiple bank cards.
15. Concurrent payment locking integrity.
16. Strict query count assertion preventing N+1 regression.
"""

from decimal import Decimal
import threading
import time

from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.debts.models import CustomerDebt
from apps.debts.services import DebtService
from apps.products.models import Product, ProductBatch
from apps.sales.models import BankCard, Payment, Sale
from apps.sales.services.sale_return_service import SaleReturnService
from apps.store.models import Store
from apps.users.models.customers import Customer
from apps.users.models.user import User


class DebtPaymentBaseTestCase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(
            phone_number="+998909870001",
            email="debt_test_user@test.uz",
            is_superuser=True,
            is_staff=True,
        )
        cls.store = Store.objects.create(
            name="Test Debt Store",
            phone_number="+998909870002",
            address="Tashkent",
            type=Store.StoreType.STORE,
        )
        cls.card = BankCard.objects.create(name="Uzcard Test", is_default=True)
        cls.card2 = BankCard.objects.create(name="Humo Test", is_default=False)
        cls.product = Product.objects.create(name="Test Spare Part")
        cls.batch = ProductBatch.objects.create(
            product=cls.product,
            store=cls.store,
            quantity=1000,
            purchase_price=Decimal("40.00"),
            selling_price=Decimal("100.00"),
        )

    def create_customer(self, name="Test Customer", phone="+998901234567"):
        return Customer.objects.create(full_name=name, phone_number=phone)

    def create_sale_with_debt(self, customer, total_amount, paid_amount=Decimal("0"), created_at=None, deleted=False):
        sale = Sale.objects.create(
            store=self.store,
            customer=customer,
            seller=self.user,
            total_amount=total_amount,
            paid_amount=paid_amount,
            status=Sale.Status.PAID if paid_amount >= total_amount else (Sale.Status.PARTIAL if paid_amount > 0 else Sale.Status.DEBT),
            payment_type=Sale.PaymentType.DEBT if paid_amount == 0 else Sale.PaymentType.CASH,
            deleted_at=timezone.now() if deleted else None,
        )
        debt_amount = total_amount - paid_amount
        if debt_amount > 0:
            CustomerDebt.objects.create(
                customer=customer,
                sale=sale,
                amount=debt_amount,
                type=CustomerDebt.Type.INCREASE,
            )
        if paid_amount > 0:
            Payment.objects.create(
                customer=customer,
                sale=sale,
                amount=paid_amount,
                type=Payment.Type.CASH,
                is_debt_payment=False,
            )
        return sale


class CustomerDebtPaymentOptimizationTests(DebtPaymentBaseTestCase):

    def test_case_1_single_debt_sale_exact_payment(self):
        """CASE 1: Customerda 1 ta qarzli Sale - exact payment."""
        c = self.create_customer("C1", "+998900000011")
        sale = self.create_sale_with_debt(c, Decimal("150.00"))

        res = DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("150.00"),
            payment_type=Payment.Type.CASH,
        )

        self.assertEqual(res["paid"], "150.00")
        self.assertEqual(res["remaining_debt"], "0.00")
        self.assertEqual(len(res["allocations"]), 1)
        self.assertTrue(res["allocations"][0]["closed"])
        self.assertEqual(res["allocations"][0]["amount"], "150.00")
        self.assertEqual(res["allocations"][0]["sale_debt_left"], "0.00")

        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.PAID)
        self.assertEqual(sale.paid_amount, Decimal("150.00"))
        self.assertEqual(sale.payment_type, Sale.PaymentType.CASH)

        # CustomerDebt decrease yozuvi tekshiruvi
        dec = CustomerDebt.objects.filter(sale=sale, type=CustomerDebt.Type.DECREASE)
        self.assertEqual(dec.count(), 1)
        self.assertEqual(dec.first().amount, Decimal("150.00"))

        # Payment yozuvi tekshiruvi
        p = Payment.objects.filter(sale=sale, is_debt_payment=True)
        self.assertEqual(p.count(), 1)
        self.assertEqual(p.first().amount, Decimal("150.00"))

    def test_case_2_ten_sales_fifo_allocation(self):
        """CASE 2: Customerda 10 ta qarzli Sale — FIFO bo'yicha eng eskisidan boshlab to'lanishi."""
        c = self.create_customer("C2", "+998900000012")
        sales = [self.create_sale_with_debt(c, Decimal("100.00")) for _ in range(10)]

        # 350 to'lov: 1-sotuv (100), 2-sotuv (100), 3-sotuv (100), 4-sotuv (50), qolganlari 0
        res = DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("350.00"),
            payment_type=Payment.Type.CASH,
        )

        self.assertEqual(res["paid"], "350.00")
        self.assertEqual(res["remaining_debt"], "650.00")
        self.assertEqual(len(res["allocations"]), 4)

        # 1, 2, 3 to'liq yopilgan
        for i in range(3):
            self.assertEqual(res["allocations"][i]["sale"], sales[i].id)
            self.assertEqual(res["allocations"][i]["amount"], "100.00")
            self.assertTrue(res["allocations"][i]["closed"])

        # 4-sotuv qisman (50) to'langan
        self.assertEqual(res["allocations"][3]["sale"], sales[3].id)
        self.assertEqual(res["allocations"][3]["amount"], "50.00")
        self.assertFalse(res["allocations"][3]["closed"])
        self.assertEqual(res["allocations"][3]["sale_debt_left"], "50.00")

        # 5-10 tegmagan
        sales[3].refresh_from_db()
        self.assertEqual(sales[3].status, Sale.Status.PARTIAL)
        self.assertEqual(sales[3].paid_amount, Decimal("50.00"))

        sales[4].refresh_from_db()
        self.assertEqual(sales[4].status, Sale.Status.DEBT)
        self.assertEqual(sales[4].paid_amount, Decimal("0.00"))

    def test_case_3_hundred_sales_scalability_and_correctness(self):
        """CASE 3: Customerda 100 ta qarzli Sale — to'liq va aniq taqsimot."""
        c = self.create_customer("C3", "+998900000013")
        sales = [self.create_sale_with_debt(c, Decimal("50.00")) for _ in range(100)]

        # 2550 to'lov: 51 ta sotuvni yopadi (51 * 50 = 2550)
        res = DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("2550.00"),
            payment_type=Payment.Type.CASH,
        )

        self.assertEqual(res["paid"], "2550.00")
        self.assertEqual(res["remaining_debt"], "2450.00")
        self.assertEqual(len(res["allocations"]), 51)
        for alloc in res["allocations"]:
            self.assertTrue(alloc["closed"])
            self.assertEqual(alloc["amount"], "50.00")

    def test_case_4_multiple_sales_partial_payment(self):
        """CASE 4: Bir nechta Sale va partial payment."""
        c = self.create_customer("C4", "+998900000014")
        s1 = self.create_sale_with_debt(c, Decimal("200.00"))
        s2 = self.create_sale_with_debt(c, Decimal("300.00"))

        res = DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("250.00"),
            payment_type=Payment.Type.CASH,
        )

        self.assertEqual(res["paid"], "250.00")
        self.assertEqual(res["remaining_debt"], "250.00")
        self.assertEqual(len(res["allocations"]), 2)

        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.status, Sale.Status.PAID)
        self.assertEqual(s1.paid_amount, Decimal("200.00"))
        self.assertEqual(s2.status, Sale.Status.PARTIAL)
        self.assertEqual(s2.paid_amount, Decimal("50.00"))

    def test_case_5_exact_debt_amount_payment(self):
        """CASE 5: Exact debt amount payment across multiple sales."""
        c = self.create_customer("C5", "+998900000015")
        s1 = self.create_sale_with_debt(c, Decimal("100.00"))
        s2 = self.create_sale_with_debt(c, Decimal("200.00"))

        res = DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("300.00"),
            payment_type=Payment.Type.CASH,
        )

        self.assertEqual(res["paid"], "300.00")
        self.assertEqual(res["remaining_debt"], "0.00")
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.status, Sale.Status.PAID)
        self.assertEqual(s2.status, Sale.Status.PAID)

    def test_case_6_debt_dan_kam_payment(self):
        """CASE 6: Qarzdan kam to'lov."""
        c = self.create_customer("C6", "+998900000016")
        s1 = self.create_sale_with_debt(c, Decimal("500.00"))

        res = DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("100.00"),
            payment_type=Payment.Type.CASH,
        )

        self.assertEqual(res["paid"], "100.00")
        self.assertEqual(res["remaining_debt"], "400.00")
        s1.refresh_from_db()
        self.assertEqual(s1.status, Sale.Status.PARTIAL)
        self.assertEqual(s1.paid_amount, Decimal("100.00"))

    def test_case_7_overpayment_rejected(self):
        """CASE 7: Qarzdan katta to'lov rad etiladi (ValidationError)."""
        c = self.create_customer("C7", "+998900000017")
        self.create_sale_with_debt(c, Decimal("100.00"))

        with self.assertRaises(DRFValidationError) as ctx:
            DebtService.pay_customer_debt(
                customer_id=c.id,
                amount=Decimal("150.00"),
                payment_type=Payment.Type.CASH,
            )
        self.assertIn("To'lov summasi umumiy qarzdan oshib ketdi", str(ctx.exception))

    def test_case_8_previous_debt_payments_exist(self):
        """CASE 8: Oldingi debt payments mavjud bo'lgan holatda ketma-ket to'lov."""
        c = self.create_customer("C8", "+998900000018")
        s1 = self.create_sale_with_debt(c, Decimal("200.00"))

        # 1-to'lov: 50 so'm
        DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("50.00"),
            payment_type=Payment.Type.CASH,
        )
        s1.refresh_from_db()
        self.assertEqual(s1.paid_amount, Decimal("50.00"))
        self.assertEqual(DebtService.get_sale_debt(s1), Decimal("150.00"))

        # 2-to'lov: 150 so'm
        res2 = DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("150.00"),
            payment_type=Payment.Type.CASH,
        )
        self.assertEqual(res2["remaining_debt"], "0.00")
        s1.refresh_from_db()
        self.assertEqual(s1.status, Sale.Status.PAID)
        self.assertEqual(s1.paid_amount, Decimal("200.00"))
        self.assertEqual(DebtService.get_sale_debt(s1), Decimal("0.00"))

    def test_case_9_returned_sale_debt_reduction(self):
        """CASE 9: Returned sale mavjud — qarz qaytarim hisobiga kamaygan bo'lsa."""
        c = self.create_customer("C9", "+998900000019")
        s1 = self.create_sale_with_debt(c, Decimal("300.00"))

        # Qaytarim hisobiga 100 so'm qarz kamaytirildi (decrease_debt)
        DebtService.decrease_debt(customer=c, sale=s1, amount=Decimal("100.00"))
        self.assertEqual(DebtService.get_sale_debt(s1), Decimal("200.00"))

        # Endi faqat qolgan 200 so'm to'lanishi kerak
        res = DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("200.00"),
            payment_type=Payment.Type.CASH,
        )
        self.assertEqual(res["paid"], "200.00")
        self.assertEqual(res["remaining_debt"], "0.00")

    def test_case_10_soft_deleted_sale_excluded(self):
        """CASE 10: Soft-deleted sale qarz to'lovidan chiqarib tashlanishi."""
        c = self.create_customer("C10", "+998900000020")
        # Deleted sale
        s_deleted = self.create_sale_with_debt(c, Decimal("100.00"), deleted=True)
        # Active sale
        s_active = self.create_sale_with_debt(c, Decimal("200.00"), deleted=False)

        # Mijozning aktiv qarzi faqat 200 bo'lishi kerak
        res = DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("200.00"),
            payment_type=Payment.Type.CASH,
        )
        self.assertEqual(res["paid"], "200.00")
        self.assertEqual(res["remaining_debt"], "0.00")
        self.assertEqual(len(res["allocations"]), 1)
        self.assertEqual(res["allocations"][0]["sale"], s_active.id)

    def test_case_11_same_period_return_against_debt(self):
        """CASE 11: Qaytarim CustomerDebt(type='d') yaratadi (is_debt_payment=False). To'lov qolganini yopadi."""
        c = self.create_customer("C11", "+998900000021")
        sale = self.create_sale_with_debt(c, Decimal("500.00"))

        # Qaytarim orqali qarz 200 ga kamaytirildi
        DebtService.decrease_debt(customer=c, sale=sale, amount=Decimal("200.00"))

        # Qarz 300 qolgan. 300 to'lash:
        res = DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("300.00"),
            payment_type=Payment.Type.CASH,
        )
        self.assertEqual(res["paid"], "300.00")
        self.assertEqual(res["remaining_debt"], "0.00")
        self.assertEqual(DebtService.get_sale_debt(sale), Decimal("0.00"))

    def test_case_12_initial_card_payment_and_later_debt_payment(self):
        """CASE 12: Boshida karta bilan to'langan qisman qarz, keyin naqd qarz to'lovi -> MIXED."""
        c = self.create_customer("C12", "+998900000022")
        # 100 so'mlik sotuv: 40 karta bilan to'langan, 60 qarz qolgan
        sale = Sale.objects.create(
            store=self.store,
            customer=c,
            seller=self.user,
            total_amount=Decimal("100.00"),
            paid_amount=Decimal("40.00"),
            status=Sale.Status.PARTIAL,
            payment_type=Sale.PaymentType.CARD,
        )
        CustomerDebt.objects.create(
            customer=c,
            sale=sale,
            amount=Decimal("60.00"),
            type=CustomerDebt.Type.INCREASE,
        )
        Payment.objects.create(
            customer=c,
            sale=sale,
            amount=Decimal("40.00"),
            type=Payment.Type.CARD,
            bank_card=self.card,
            is_debt_payment=False,
        )

        res = DebtService.pay_customer_debt(
            customer_id=c.id,
            amount=Decimal("60.00"),
            payment_type=Payment.Type.CASH,
        )
        self.assertEqual(res["paid"], "60.00")
        self.assertEqual(res["remaining_debt"], "0.00")

        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.PAID)
        self.assertEqual(sale.paid_amount, Decimal("100.00"))
        # 40 karta + 60 naqd -> MIXED
        self.assertEqual(sale.payment_type, Sale.PaymentType.MIXED)

    def test_case_13_customer_has_no_debt(self):
        """CASE 13: Customerda qarz yo'q bo'lsa ValidationError chiqishi."""
        c = self.create_customer("C13", "+998900000023")
        # Qarzli sotuv yaratilmagan

        with self.assertRaises(DRFValidationError) as ctx:
            DebtService.pay_customer_debt(
                customer_id=c.id,
                amount=Decimal("100.00"),
                payment_type=Payment.Type.CASH,
            )
        self.assertIn("Mijozda qarz yo'q", str(ctx.exception))

    def test_case_14_split_payment_with_multiple_cards(self):
        """CASE 14: Split payment — naqd + karta1 + karta2 bilan qarz to'lash."""
        c = self.create_customer("C14", "+998900000024")
        s1 = self.create_sale_with_debt(c, Decimal("100.00"))
        s2 = self.create_sale_with_debt(c, Decimal("100.00"))

        res = DebtService.pay_customer_debt(
            customer_id=c.id,
            payments=[
                {"type": "cash", "amount": Decimal("50.00")},
                {"type": "card", "amount": Decimal("50.00"), "bank_card": self.card},
                {"type": "card", "amount": Decimal("100.00"), "bank_card": self.card2},
            ],
        )

        self.assertEqual(res["paid"], "200.00")
        self.assertEqual(res["remaining_debt"], "0.00")
        self.assertEqual(len(res["allocations"]), 2)

        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.status, Sale.Status.PAID)
        self.assertEqual(s2.status, Sale.Status.PAID)

        # Har bir sotuv bo'yicha to'lovlar yaratilganligini tekshirish
        s1_payments = list(s1.payments.filter(is_debt_payment=True).order_by("created_at", "id"))
        self.assertEqual(len(s1_payments), 2)  # 50 naqd, 50 karta1
        self.assertEqual(s1_payments[0].type, "cash")
        self.assertEqual(s1_payments[0].amount, Decimal("50.00"))
        self.assertEqual(s1_payments[1].type, "card")
        self.assertEqual(s1_payments[1].bank_card, self.card)
        self.assertEqual(s1_payments[1].amount, Decimal("50.00"))

        s2_payments = list(s2.payments.filter(is_debt_payment=True))
        self.assertEqual(len(s2_payments), 1)  # 100 karta2
        self.assertEqual(s2_payments[0].type, "card")
        self.assertEqual(s2_payments[0].bank_card, self.card2)
        self.assertEqual(s2_payments[0].amount, Decimal("100.00"))

    def test_case_15_query_count_strictly_o1_regression(self):
        """
        CASE 15: Query-Count Regression Test.
        10 ta, 50 ta va 100 ta sotuv uchun DB query count O(1) ekanligini isbotlash.
        Har bir holatda so'rovlar soni bir xil (<= 8 queries, shu jumladan test savepointlari) bo'lishi kerak.
        """
        # Test 10 sales
        c10 = self.create_customer("C_QC_10", "+998900000025")
        for _ in range(10):
            self.create_sale_with_debt(c10, Decimal("100.00"))

        with CaptureQueriesContext(connection) as ctx10:
            DebtService.pay_customer_debt(
                customer_id=c10.id,
                amount=Decimal("1000.00"),
                payment_type=Payment.Type.CASH,
            )
        q10_count = len(ctx10.captured_queries)

        # Test 50 sales
        c50 = self.create_customer("C_QC_50", "+998900000026")
        for _ in range(50):
            self.create_sale_with_debt(c50, Decimal("100.00"))

        with CaptureQueriesContext(connection) as ctx50:
            DebtService.pay_customer_debt(
                customer_id=c50.id,
                amount=Decimal("5000.00"),
                payment_type=Payment.Type.CASH,
            )
        q50_count = len(ctx50.captured_queries)

        # Test 100 sales
        c100 = self.create_customer("C_QC_100", "+998900000027")
        for _ in range(100):
            self.create_sale_with_debt(c100, Decimal("100.00"))

        with CaptureQueriesContext(connection) as ctx100:
            DebtService.pay_customer_debt(
                customer_id=c100.id,
                amount=Decimal("10000.00"),
                payment_type=Payment.Type.CASH,
            )
        q100_count = len(ctx100.captured_queries)

        # Strict assertion: query count does NOT scale with N!
        self.assertEqual(q10_count, q50_count)
        self.assertEqual(q50_count, q100_count)
        self.assertLessEqual(q100_count, 8)  # Exactly 6 business queries + 2 savepoints


class CustomerDebtConcurrencyTests(TransactionTestCase):
    """
    CASE 16: Concurrency / Transaction Safety.
    Bir vaqtning o'zida bir xil customer uchun ikkita pay_customer_debt yuborilganda,
    select_for_update qulfi ishonchli ishlashi va qarz ikki marta kamayib ketmasligi.
    """

    def setUp(self):
        self.user = User.objects.create(
            phone_number="+998909879901",
            email="conc_debt@test.uz",
            is_superuser=True,
            is_staff=True,
        )
        self.store = Store.objects.create(
            name="Conc Debt Store",
            phone_number="+998909879902",
            address="Tashkent",
            type=Store.StoreType.STORE,
        )
        self.customer = Customer.objects.create(
            full_name="Conc Customer",
            phone_number="+998909879903",
        )

    def test_concurrent_debt_payments_prevent_double_spend(self):
        # 100 so'mlik bitta qarzli sotuv
        sale = Sale.objects.create(
            store=self.store,
            customer=self.customer,
            seller=self.user,
            total_amount=Decimal("100.00"),
            paid_amount=Decimal("0.00"),
            status=Sale.Status.DEBT,
            payment_type=Sale.PaymentType.DEBT,
        )
        CustomerDebt.objects.create(
            customer=self.customer,
            sale=sale,
            amount=Decimal("100.00"),
            type=CustomerDebt.Type.INCREASE,
        )

        results = []
        errors = []

        def worker():
            try:
                res = DebtService.pay_customer_debt(
                    customer_id=self.customer.id,
                    amount=Decimal("100.00"),
                    payment_type=Payment.Type.CASH,
                )
                results.append(res)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Bittasi muvaffaqiyatli o'tadi, ikkinchisi "Mijozda qarz yo'q" deb xatolik qaytaradi
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], DRFValidationError)
        self.assertIn("Mijozda qarz yo'q", str(errors[0]))

        # Qarz aynan 1 marta kamaygan
        self.assertEqual(DebtService.get_sale_debt(sale), Decimal("0.00"))
        sale.refresh_from_db()
        self.assertEqual(sale.paid_amount, Decimal("100.00"))
        self.assertEqual(sale.status, Sale.Status.PAID)


class CustomerPayDebtAPITests(DebtPaymentBaseTestCase):
    """
    API Contract Verification: POST /api/debts/customer/pay/
    HTTP status, request/response payload format, validation errors.
    """

    def setUp(self):
        from rest_framework.test import APIClient
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_api_pay_customer_debt_success(self):
        c = self.create_customer("API Customer", "+998909991122")
        self.create_sale_with_debt(c, Decimal("120.00"))

        response = self.client.post(
            "/api/debts/customer/pay/",
            data={
                "customer": c.id,
                "amount": "120.00",
                "type": "cash",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["message"], "Qarz to'lovi qabul qilindi")
        self.assertEqual(data["paid"], "120.00")
        self.assertEqual(data["remaining_debt"], "0.00")
        self.assertEqual(len(data["allocations"]), 1)
        self.assertTrue(data["allocations"][0]["closed"])

    def test_api_pay_customer_debt_split_success(self):
        c = self.create_customer("API Split Customer", "+998909991133")
        self.create_sale_with_debt(c, Decimal("150.00"))

        response = self.client.post(
            "/api/debts/customer/pay/",
            data={
                "customer": c.id,
                "payments": [
                    {"type": "cash", "amount": "50.00"},
                    {"type": "card", "amount": "100.00", "bank_card": self.card.id},
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["paid"], "150.00")
        self.assertEqual(data["remaining_debt"], "0.00")
        self.assertEqual(len(data["allocations"]), 1)
        self.assertEqual(len(data["allocations"][0]["payment_ids"]), 2)

