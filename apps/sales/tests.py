"""
BankCard / Payment / Sale.payment_type funksionalligi uchun testlar.

Ishga tushirish:
    python manage.py test apps.sales
"""

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.debts.services import DebtService
from apps.products.models import Product, ProductBatch
from apps.sales.models import BankCard, Payment, Sale
from apps.sales.payment_rules import compute_payment_type
from apps.sales.serializers import PaymentInputSerializer
from apps.sales.services import SaleService
from apps.sales.services.sale_return_service import SaleReturnService
from apps.store.models import Store, StoreUser
from apps.users.models.customers import Customer
from apps.users.models.user import User


class PaymentRulesTest(TestCase):
    """Markaziy compute_payment_type qoidasi — 4 holat."""

    def test_rules(self):
        zero = Decimal("0")
        ten = Decimal("10")
        self.assertEqual(compute_payment_type(zero, zero), "debt")
        self.assertEqual(compute_payment_type(ten, zero), "cash")
        self.assertEqual(compute_payment_type(zero, ten), "card")
        self.assertEqual(compute_payment_type(ten, ten), "mixed")
        self.assertEqual(compute_payment_type(None, None), "debt")


class BankCardTest(TestCase):

    def test_single_default_card(self):
        """Bir vaqtda faqat bitta karta default bo'lishi mumkin."""
        a = BankCard.objects.create(name="Uzcard", is_default=True)
        b = BankCard.objects.create(name="Humo", is_default=True)

        a.refresh_from_db()
        b.refresh_from_db()

        self.assertFalse(a.is_default)
        self.assertTrue(b.is_default)
        self.assertEqual(BankCard.objects.filter(is_default=True).count(), 1)


class PaymentModelValidationTest(TestCase):

    def setUp(self):
        self.card = BankCard.objects.create(name="Uzcard")

    def test_card_payment_requires_bank_card(self):
        with self.assertRaises(DjangoValidationError):
            Payment.objects.create(amount=Decimal("10"), type=Payment.Type.CARD)

    def test_cash_payment_forbids_bank_card(self):
        with self.assertRaises(DjangoValidationError):
            Payment.objects.create(
                amount=Decimal("10"), type=Payment.Type.CASH, bank_card=self.card
            )

    def test_valid_payments(self):
        Payment.objects.create(amount=Decimal("10"), type=Payment.Type.CASH)
        Payment.objects.create(
            amount=Decimal("10"), type=Payment.Type.CARD, bank_card=self.card
        )
        self.assertEqual(Payment.objects.count(), 2)


class PaymentInputSerializerTest(TestCase):

    def setUp(self):
        self.card = BankCard.objects.create(name="Uzcard")
        self.inactive = BankCard.objects.create(name="Eski karta", is_active=False)

    def test_card_without_bank_card_invalid(self):
        s = PaymentInputSerializer(data={"type": "card", "amount": "10.00"})
        self.assertFalse(s.is_valid())
        self.assertIn("bank_card", s.errors)

    def test_cash_with_bank_card_invalid(self):
        s = PaymentInputSerializer(
            data={"type": "cash", "amount": "10.00", "bank_card": self.card.id}
        )
        self.assertFalse(s.is_valid())
        self.assertIn("bank_card", s.errors)

    def test_inactive_card_invalid(self):
        s = PaymentInputSerializer(
            data={"type": "card", "amount": "10.00", "bank_card": self.inactive.id}
        )
        self.assertFalse(s.is_valid())

    def test_valid_card_payment(self):
        s = PaymentInputSerializer(
            data={"type": "card", "amount": "10.00", "bank_card": self.card.id}
        )
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data["bank_card"], self.card)


class SaleFlowTestMixin:
    """Sotuv oqimlari uchun umumiy test ma'lumotlari."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(
            phone_number="+998900000001",
            email="admin@test.uz",
            is_superuser=True,
            is_staff=True,
        )
        cls.store = Store.objects.create(
            name="Test do'kon", phone_number="+998900000002",
            address="Test", type=Store.StoreType.STORE,
        )
        cls.customer = Customer.objects.create(
            full_name="Test mijoz", phone_number="+998900000003"
        )
        cls.product = Product.objects.create(name="Moy filtri")
        cls.batch = ProductBatch.objects.create(
            product=cls.product, store=cls.store,
            quantity=100,
            purchase_price=Decimal("50"), selling_price=Decimal("100"),
        )
        cls.card = BankCard.objects.create(name="Uzcard", is_default=True)
        cls.card2 = BankCard.objects.create(name="Humo")

    def make_sale(self, payments, quantity=2, price="100.00", customer=None, due=None):
        return SaleService.create_sale(
            user=self.user,
            data={
                "store": self.store.id,
                "customer": customer.id if customer else None,
                "items": [{
                    "product": self.product.id,
                    "quantity": quantity,
                    "price": Decimal(price),
                }],
                "payments": payments,
                "debt_due_date": due,
            },
        )


class SaleCreatePaymentTypeTest(SaleFlowTestMixin, TestCase):

    def test_cash_only(self):
        sale = self.make_sale([{"type": "cash", "amount": Decimal("200")}])
        self.assertEqual(sale.payment_type, Sale.PaymentType.CASH)
        self.assertEqual(sale.status, Sale.Status.PAID)

    def test_card_only(self):
        sale = self.make_sale([
            {"type": "card", "amount": Decimal("200"), "bank_card": self.card}
        ])
        self.assertEqual(sale.payment_type, Sale.PaymentType.CARD)
        payment = sale.payments.get()
        self.assertEqual(payment.bank_card, self.card)

    def test_mixed(self):
        """Yarmi naqd, yarmi karta → MIXED va ikkala to'lov ham saqlanadi."""
        sale = self.make_sale([
            {"type": "cash", "amount": Decimal("120")},
            {"type": "card", "amount": Decimal("80"), "bank_card": self.card},
        ])
        self.assertEqual(sale.payment_type, Sale.PaymentType.MIXED)
        self.assertEqual(sale.payments.count(), 2)
        self.assertEqual(sale.paid_amount, Decimal("200"))

    def test_partial_cash_is_cash(self):
        sale = self.make_sale(
            [{"type": "cash", "amount": Decimal("50")}],
            customer=self.customer,
        )
        self.assertEqual(sale.payment_type, Sale.PaymentType.CASH)
        self.assertEqual(sale.status, Sale.Status.PARTIAL)


class PayDebtPaymentTypeTest(SaleFlowTestMixin, TestCase):

    def test_debt_paid_by_card_becomes_mixed(self):
        """Naqd qisman to'lov + keyin karta bilan qarz yopish → MIXED."""
        sale = self.make_sale(
            [{"type": "cash", "amount": Decimal("50")}],
            customer=self.customer,
        )
        DebtService.increase_debt(
            customer=self.customer, sale=sale, amount=Decimal("150")
        )

        DebtService.pay_debt(
            sale_id=sale.id,
            amount=Decimal("150"),
            payment_type=Payment.Type.CARD,
            bank_card=self.card,
        )

        sale.refresh_from_db()
        self.assertEqual(sale.payment_type, Sale.PaymentType.MIXED)
        card_payment = sale.payments.get(type=Payment.Type.CARD)
        self.assertEqual(card_payment.bank_card, self.card)


class SaleReturnPaymentTest(SaleFlowTestMixin, TestCase):

    def _paid_mixed_sale(self):
        return self.make_sale([
            {"type": "cash", "amount": Decimal("120")},
            {"type": "card", "amount": Decimal("80"), "bank_card": self.card},
        ], customer=self.customer)

    def test_return_with_card_refund(self):
        """Qaytarim kartaga qaytarilsa — is_refund=True bo'lib yoziladi va NET qayta hisoblanadi."""
        sale = self._paid_mixed_sale()
        sale_item = sale.items.get()

        SaleReturnService.create_return(
            user=self.user,
            data={
                "sale": sale.id,
                "items": [{"sale_item": sale_item.id, "quantity": 1}],  # 100 so'm
                "payments": [
                    {"type": "card", "amount": Decimal("80"), "bank_card": self.card},
                    {"type": "cash", "amount": Decimal("20")},
                ],
            },
        )

        sale.refresh_from_db()
        refunds = sale.payments.filter(is_refund=True)
        self.assertEqual(refunds.count(), 2)
        # NET: cash 120-20=100 > 0, card 80-80=0 → CASH
        self.assertEqual(sale.payment_type, Sale.PaymentType.CASH)

    def test_return_payments_sum_mismatch(self):
        sale = self._paid_mixed_sale()
        sale_item = sale.items.get()

        with self.assertRaises(DRFValidationError):
            SaleReturnService.create_return(
                user=self.user,
                data={
                    "sale": sale.id,
                    "items": [{"sale_item": sale_item.id, "quantity": 1}],
                    "payments": [{"type": "cash", "amount": Decimal("999")}],
                },
            )

    def test_return_without_payments_defaults_to_cash(self):
        """Eski xatti-harakat saqlanadi: payments yuborilmasa refund naqd yoziladi."""
        sale = self._paid_mixed_sale()
        sale_item = sale.items.get()

        SaleReturnService.create_return(
            user=self.user,
            data={
                "sale": sale.id,
                "items": [{"sale_item": sale_item.id, "quantity": 1}],
            },
        )

        refund = Sale.objects.get(pk=sale.pk).payments.get(is_refund=True)
        self.assertEqual(refund.type, Payment.Type.CASH)
        self.assertEqual(refund.amount, Decimal("100"))


class SaleDiscountRoundingTest(SaleFlowTestMixin, TestCase):
    """
    Foizli chegirma kasr qoldiq bermasligi kerak — aks holda kassada to'liq
    to'langan sotuvda "arvoh qarz" paydo bo'lib, yakunlash bloklanardi.
    """

    def _sale_with_percentage_discount(self, unit_price, quantity, percent, paid):
        return SaleService.create_sale(
            user=self.user,
            data={
                "store": self.store.id,
                "customer": None,
                "items": [{
                    "product": self.product.id,
                    "quantity": quantity,
                    "price": Decimal(unit_price),
                }],
                "discount_type": Sale.DiscountType.PERCENTAGE,
                "discount_value": Decimal(percent),
                "payments": [{"type": "cash", "amount": Decimal(paid)}],
            },
        )

    def test_fractional_discount_is_rounded_and_sale_is_paid(self):
        # 3 x 412 = 1236, 10% chegirma → 1112.40 → 1112 ga yaxlitlanadi
        sale = self._sale_with_percentage_discount("412", 3, "10", "1112")

        self.assertEqual(sale.total_amount, Decimal("1112"))
        self.assertEqual(sale.status, Sale.Status.PAID)
        # Chegirma yaxlitlangan jamiga mos: subtotal - discount == total
        self.assertEqual(sale.discount_amount, Decimal("124"))
        self.assertEqual(
            sale.total_amount + sale.discount_amount, Decimal("1236")
        )

    def test_half_rounds_up_like_frontend(self):
        # 5 x 247 = 1235, 10% → 1111.50 → ROUND_HALF_UP → 1112 (JS Math.round bilan bir xil)
        sale = self._sale_with_percentage_discount("247", 5, "10", "1112")

        self.assertEqual(sale.total_amount, Decimal("1112"))
        self.assertEqual(sale.status, Sale.Status.PAID)

    def test_whole_discount_is_unchanged(self):
        # 2 x 100 = 200, 30% → 140 (kasr yo'q, o'zgarmasligi kerak)
        sale = self._sale_with_percentage_discount("100", 2, "30", "140")

        self.assertEqual(sale.total_amount, Decimal("140"))
        self.assertEqual(sale.discount_amount, Decimal("60"))
        self.assertEqual(sale.status, Sale.Status.PAID)


class SaleReturnDiscountTest(SaleFlowTestMixin, TestCase):
    """Chegirmali sotuvni qaytarganda do'kon olmagan pulini qaytarmasligi kerak."""

    def _discounted_sale(self):
        # 2 x 100 = 200, 30% chegirma → mijoz 140 to'laydi
        sale = SaleService.create_sale(
            user=self.user,
            data={
                "store": self.store.id,
                "customer": None,
                "items": [{
                    "product": self.product.id,
                    "quantity": 2,
                    "price": Decimal("100.00"),
                }],
                "discount_type": Sale.DiscountType.PERCENTAGE,
                "discount_value": Decimal("30"),
                "payments": [{"type": "cash", "amount": Decimal("140")}],
            },
        )
        return sale

    def test_full_return_refunds_only_what_was_paid(self):
        sale = self._discounted_sale()
        self.assertEqual(sale.discount_amount, Decimal("60"))
        self.assertEqual(sale.paid_amount, Decimal("140"))

        sale_item = sale.items.get()
        return_obj = SaleReturnService.create_return(
            user=self.user,
            data={
                "sale": sale.id,
                "items": [{"sale_item": sale_item.id, "quantity": 2}],
            },
        )

        # Gross 200 emas, chegirmali 140 qaytarilishi kerak
        self.assertEqual(return_obj.total_refund, Decimal("140.00"))

    def test_partial_return_is_prorated(self):
        sale = self._discounted_sale()
        sale_item = sale.items.get()

        return_obj = SaleReturnService.create_return(
            user=self.user,
            data={
                "sale": sale.id,
                "items": [{"sale_item": sale_item.id, "quantity": 1}],
            },
        )

        # 1 dona: 100 * 0.7 = 70
        self.assertEqual(return_obj.total_refund, Decimal("70.00"))

    def test_sale_without_discount_refunds_gross(self):
        sale = self.make_sale([{"type": "cash", "amount": Decimal("200")}])
        sale_item = sale.items.get()

        return_obj = SaleReturnService.create_return(
            user=self.user,
            data={
                "sale": sale.id,
                "items": [{"sale_item": sale_item.id, "quantity": 2}],
            },
        )

        self.assertEqual(return_obj.total_refund, Decimal("200.00"))


class SaleReturnDuplicateItemTest(SaleFlowTestMixin, TestCase):
    """Bitta sale_item so'rovda ikki marta kelsa 500 bermasligi kerak."""

    def test_duplicate_sale_item_is_merged(self):
        sale = self.make_sale([{"type": "cash", "amount": Decimal("200")}], quantity=2)
        sale_item = sale.items.get()

        return_obj = SaleReturnService.create_return(
            user=self.user,
            data={
                "sale": sale.id,
                "items": [
                    {"sale_item": sale_item.id, "quantity": 1},
                    {"sale_item": sale_item.id, "quantity": 1},
                ],
            },
        )

        self.assertEqual(return_obj.total_refund, Decimal("200.00"))
        sale_item.refresh_from_db()
        self.assertEqual(sale_item.returned_quantity, 2)

    def test_duplicate_exceeding_available_is_rejected(self):
        sale = self.make_sale([{"type": "cash", "amount": Decimal("200")}], quantity=2)
        sale_item = sale.items.get()

        with self.assertRaises(DRFValidationError):
            SaleReturnService.create_return(
                user=self.user,
                data={
                    "sale": sale.id,
                    "items": [
                        {"sale_item": sale_item.id, "quantity": 2},
                        {"sale_item": sale_item.id, "quantity": 1},
                    ],
                },
            )


class SaleReturnStoreScopeTest(SaleFlowTestMixin, TestCase):
    """Boshqa do'kon sotuvini qaytarib bo'lmaydi."""

    def test_foreign_store_sale_is_denied(self):
        sale = self.make_sale([{"type": "cash", "amount": Decimal("200")}])
        sale_item = sale.items.get()

        outsider = User.objects.create(
            phone_number="+998900000099", email="outsider@test.uz"
        )
        other_store = Store.objects.create(
            name="Boshqa do'kon", phone_number="+998900000098",
            address="Boshqa", type=Store.StoreType.STORE,
        )
        StoreUser.objects.create(user=outsider, store=other_store, is_active=True)

        with self.assertRaises(PermissionDenied):
            SaleReturnService.create_return(
                user=outsider,
                data={
                    "sale": sale.id,
                    "items": [{"sale_item": sale_item.id, "quantity": 1}],
                },
            )

    def test_missing_sale_raises_not_found(self):
        with self.assertRaises(NotFound):
            SaleReturnService.create_return(
                user=self.user,
                data={"sale": 999999, "items": []},
            )
