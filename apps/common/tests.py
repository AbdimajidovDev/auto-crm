from decimal import Decimal
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.common.quantity import (
    PAIR_STEP,
    SINGLE_STEP,
    QuantityField,
    as_quantity,
    validate_items_quantity_steps,
    validate_quantity_step,
)
from apps.contract.models import StockEntry, StockEntryItem, Supplier
from apps.contract.services.stock_entry_service import StockEntryService
from apps.inventory.models import InventorySession
from apps.inventory.services.inventory_service import InventoryService
from apps.products.models import Product, ProductBatch
from apps.sales.models import Sale, SaleItem
from apps.sales.services import SaleService
from apps.sales.services.sale_return_service import SaleReturnService
from apps.store.models import Store, StoreUser
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.transfer.serializers import TransferItemSerializer
from apps.transfer.services.transfer_service import TransferService
from apps.users.models.user import User


class QuantityValidationDonaTests(TestCase):
    """Product.is_pair = False (Dona) bo'yicha qat'iy tekshiruvlar: faqat butun sonlar (1, 2, 3...)."""

    def test_valid_dona_quantities(self):
        for val in [1, 2, 5, 10, "1", "2", "10", Decimal("1"), Decimal("10")]:
            qty = validate_quantity_step(val, is_pair=False)
            self.assertEqual(qty % 1, Decimal("0"))

    def test_invalid_dona_quantities(self):
        invalid_values = [0.25, 0.5, 0.75, 1.25, 1.5, 2.75, "0.25", "0.5", "1.5", Decimal("0.25"), Decimal("0.5")]
        for val in invalid_values:
            with self.assertRaises(ValidationError) as ctx:
                validate_quantity_step(val, is_pair=False)
            self.assertIn("butun son bo'lishi kerak", str(ctx.exception))

    def test_zero_and_negative_dona(self):
        with self.assertRaises(ValidationError):
            validate_quantity_step(0, is_pair=False, allow_zero=False)
        with self.assertRaises(ValidationError):
            validate_quantity_step(-1, is_pair=False)
        # allow_zero=True joiz
        self.assertEqual(validate_quantity_step(0, is_pair=False, allow_zero=True), Decimal("0"))


class QuantityValidationJuftTests(TestCase):
    """Product.is_pair = True (Juft) bo'yicha qat'iy tekshiruvlar: 0.25 karrali (0.25, 0.50, 0.75, 1.00...)."""

    def test_valid_juft_quantities(self):
        valid_values = [
            0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 1.75, 2.00, 5.25, 10.75,
            "0.25", "0.5", "0.75", "1.00", "1.25", "1.50", "1.75", "2.00",
            Decimal("0.25"), Decimal("0.50"), Decimal("0.75"), Decimal("1.25"),
        ]
        for val in valid_values:
            qty = validate_quantity_step(val, is_pair=True)
            self.assertEqual((qty * 4) % 1, Decimal("0"))

    def test_invalid_juft_quantities(self):
        invalid_values = [
            0.1, 0.125, 0.2, 0.3, 0.33, 0.7, 0.8, 1.1, 1.2, 2.35,
            "0.1", "0.125", "0.3", "0.7", Decimal("0.1"), Decimal("0.3"),
        ]
        for val in invalid_values:
            with self.assertRaises(ValidationError) as ctx:
                validate_quantity_step(val, is_pair=True)
            self.assertIn("0.25 ga karrali", str(ctx.exception))

    def test_zero_and_negative_juft(self):
        with self.assertRaises(ValidationError):
            validate_quantity_step(0, is_pair=True, allow_zero=False)
        with self.assertRaises(ValidationError):
            validate_quantity_step(-0.25, is_pair=True)
        # allow_zero=True joiz
        self.assertEqual(validate_quantity_step(0, is_pair=True, allow_zero=True), Decimal("0"))


class QuantityFieldSerializerTests(TestCase):
    """QuantityField DRF serializer maydoni darajasidagi tekshiruvlar."""

    def test_quantity_field_valid_and_invalid(self):
        field = QuantityField()
        self.assertEqual(field.to_internal_value("0.25"), Decimal("0.25"))
        self.assertEqual(field.to_internal_value("0.5"), Decimal("0.50"))
        self.assertEqual(field.to_internal_value("0.75"), Decimal("0.75"))
        self.assertEqual(field.to_internal_value("1"), Decimal("1.00"))
        self.assertEqual(field.to_internal_value("2.25"), Decimal("2.25"))

        with self.assertRaises(ValidationError):
            field.to_internal_value("0.1")
        with self.assertRaises(ValidationError):
            field.to_internal_value("0.3")


class FinancialArithmeticTests(TestCase):
    """Moliyaviy hisob-kitoblar aniqligi: floating point xatolarisiz."""

    def test_exact_price_multiplications(self):
        # 10000 × 0.25 = 2500
        self.assertEqual(Decimal("10000") * Decimal("0.25"), Decimal("2500.00"))
        # 12500 × 0.25 = 3125
        self.assertEqual(Decimal("12500") * Decimal("0.25"), Decimal("3125.00"))
        # 10000 × 0.75 = 7500
        self.assertEqual(Decimal("10000") * Decimal("0.75"), Decimal("7500.00"))
        # 12500 × 0.75 = 9375
        self.assertEqual(Decimal("12500") * Decimal("0.75"), Decimal("9375.00"))
        # 12345 × 0.25 = 3086.25
        self.assertEqual(Decimal("12345") * Decimal("0.25"), Decimal("3086.25"))


class BusinessFlowQuantityTests(TestCase):
    """Sotuv, Qaytarish, O'tkazma, Inventarizatsiya va Kirim biznes oqimlari testlari."""

    def setUp(self):
        self.store_a = Store.objects.create(name="Do'kon A", is_active=True)
        self.store_b = Store.objects.create(name="Do'kon B", is_active=True)
        self.user = User.objects.create(
            phone_number="+998901112233",
            full_name="Kassir Aliyev",
            is_superuser=True,
            is_staff=True,
        )
        StoreUser.objects.create(user=self.user, store=self.store_a)

        self.pair_product = Product.objects.create(
            name="Fara juft",
            sku="FARA-01",
            is_pair=True,
            status=Product.ProductStatus.ACTIVE,
        )
        self.single_product = Product.objects.create(
            name="Moy filtr",
            sku="FILTR-01",
            is_pair=False,
            status=Product.ProductStatus.ACTIVE,
        )

        # Do'kon A partiyalari
        self.batch_pair_a = ProductBatch.objects.create(
            store=self.store_a,
            product=self.pair_product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("8000"),
            selling_price=Decimal("10000"),
            wholesale_price=Decimal("9000"),
        )
        self.batch_single_a = ProductBatch.objects.create(
            store=self.store_a,
            product=self.single_product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("4000"),
            selling_price=Decimal("5000"),
            wholesale_price=Decimal("4500"),
        )
        self.supplier = Supplier.objects.create(name="Test Ta'minotchi")

    def test_sale_flow_with_pair_quantities(self):
        """Juft mahsulotni 0.25, 0.5, 0.75, 1.25 miqdorlarda sotish."""
        for step_qty, expected_total in [
            (Decimal("0.25"), Decimal("2500")),
            (Decimal("0.50"), Decimal("5000")),
            (Decimal("0.75"), Decimal("7500")),
            (Decimal("1.25"), Decimal("12500")),
        ]:
            sale = SaleService.create_sale(
                user=self.user,
                data={
                    "store": self.store_a.id,
                    "items": [{"product": self.pair_product.id, "quantity": step_qty, "price": Decimal("10000")}],
                    "payments": [{"type": "cash", "amount": expected_total}],
                },
            )
            self.assertEqual(sale.total_amount, expected_total)
            self.assertEqual(sale.items.first().quantity, step_qty)

    def test_sale_flow_dona_rejects_fraction(self):
        """Dona mahsulotni 0.5 yoki 0.25 bilan sotish rad etiladi."""
        with self.assertRaises(ValidationError):
            SaleService.create_sale(
                user=self.user,
                data={
                    "store": self.store_a.id,
                    "items": [{"product": self.single_product.id, "quantity": Decimal("0.25"), "price": Decimal("5000")}],
                    "payments": [{"type": "cash", "amount": Decimal("1250")}],
                },
            )

    def test_return_flow_pair(self):
        """Sale = 0.5 juft bo'lsa: 0.25 return -> OK; 0.75 return -> ERROR."""
        sale = SaleService.create_sale(
            user=self.user,
            data={
                "store": self.store_a.id,
                "items": [{"product": self.pair_product.id, "quantity": Decimal("0.5"), "price": Decimal("10000")}],
                "payments": [{"type": "cash", "amount": Decimal("5000")}],
            },
        )
        sale_item = sale.items.first()

        # 0.25 return -> OK
        ret = SaleReturnService.create_return(
            user=self.user,
            data={
                "sale": sale.id,
                "items": [{"sale_item": sale_item.id, "quantity": Decimal("0.25")}],
                "payments": [{"type": "cash", "amount": Decimal("2500")}],
            },
        )
        self.assertEqual(ret.total_refund, Decimal("2500.00"))
        sale_item.refresh_from_db()
        self.assertEqual(sale_item.returned_quantity, Decimal("0.25"))

        # Qolgan 0.25 dan ortiqcha (0.50) return qilinsa -> ERROR
        with self.assertRaises(ValidationError):
            SaleReturnService.create_return(
                user=self.user,
                data={
                    "sale": sale.id,
                    "items": [{"sale_item": sale_item.id, "quantity": Decimal("0.50")}],
                    "payments": [{"type": "cash", "amount": Decimal("5000")}],
                },
            )

    def test_transfer_validation(self):
        """Transfer: pair=0.25 -> OK; single=2 -> OK; single=0.5 -> ERROR."""
        # Pair 0.25 -> OK
        serializer_pair = TransferItemSerializer(
            data={"product": self.pair_product.id, "quantity": Decimal("0.25")}
        )
        self.assertTrue(serializer_pair.is_valid(), serializer_pair.errors)

        # Single 2 -> OK
        serializer_single_ok = TransferItemSerializer(
            data={"product": self.single_product.id, "quantity": Decimal("2")}
        )
        self.assertTrue(serializer_single_ok.is_valid(), serializer_single_ok.errors)

        # Single 0.5 -> ERROR
        serializer_single_err = TransferItemSerializer(
            data={"product": self.single_product.id, "quantity": Decimal("0.5")}
        )
        self.assertFalse(serializer_single_err.is_valid())

    def test_inventory_count_validation(self):
        """Inventory: pair count=10.25 -> OK; single count=10.25 -> ERROR."""
        session = InventorySession.objects.create(store=self.store_a, started_by=self.user)

        # Pair count 10.25 -> OK
        InventoryService.scan_product(
            session_id=session.id,
            product_id=self.pair_product.id,
            quantity=Decimal("10.25"),
        )
        # Single count 10.25 -> ERROR
        with self.assertRaises(ValidationError):
            InventoryService.scan_product(
                session_id=session.id,
                product_id=self.single_product.id,
                quantity=Decimal("10.25"),
            )

    def test_stock_entry_validation(self):
        """Stock Entry: pair=5.75 -> OK; single=5.75 -> ERROR."""
        # Pair 5.75 -> OK
        entry_pair = StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_a,
            user=self.user,
            items=[{
                "product": self.pair_product,
                "quantity": Decimal("5.75"),
                "purchase_price": Decimal("8000"),
                "selling_price": Decimal("10000"),
                "wholesale_price": Decimal("9000"),
            }],
        )
        self.assertEqual(entry_pair.items.first().quantity, Decimal("5.75"))

        # Single 5.75 -> ERROR
        with self.assertRaises(ValidationError):
            StockEntryService.create_entry(
                supplier=self.supplier,
                store=self.store_a,
                user=self.user,
                items=[{
                    "product": self.single_product,
                    "quantity": Decimal("5.75"),
                    "purchase_price": Decimal("4000"),
                    "selling_price": Decimal("5000"),
                    "wholesale_price": Decimal("4500"),
                }],
            )
