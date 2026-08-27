from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.inventory.models import StockAdjustment
from apps.products.models import Product, ProductBatch, ProductUnitMeasurement
from apps.store.models import Store, StoreUser
from apps.users.models import User


class ProductUpdateStocksAPITests(TestCase):
    """Product Edit orqali barcha do'konlar qoldig'i va MinStock'ni yangilash testlari."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(
            phone_number="+998901234567",
            full_name="Admin User",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_authenticate(user=self.user)

        self.store1 = Store.objects.create(name="112-do'kon", is_active=True)
        self.store2 = Store.objects.create(name="115-do'kon", is_active=True)
        self.store3 = Store.objects.create(name="96-do'kon", is_active=True)
        StoreUser.objects.create(user=self.user, store=self.store1)
        StoreUser.objects.create(user=self.user, store=self.store2)
        StoreUser.objects.create(user=self.user, store=self.store3)

        self.unit_dona = ProductUnitMeasurement.objects.create(
            measurement="dona",
            quantity_type=ProductUnitMeasurement.QuantityType.WHOLE,
        )
        self.unit_juft = ProductUnitMeasurement.objects.create(
            measurement="пара",
            quantity_type=ProductUnitMeasurement.QuantityType.QUARTER,
        )

        self.prod_dona = Product.objects.create(
            name="Amortizator",
            sku="AMORT-01",
            unit_measurement=self.unit_dona,
            min_stock=Decimal("10"),
            status=Product.ProductStatus.ACTIVE,
        )
        self.prod_juft = Product.objects.create(
            name="Podshipnik juft",
            sku="PODSH-01",
            unit_measurement=self.unit_juft,
            min_stock=Decimal("5.00"),
            status=Product.ProductStatus.ACTIVE,
        )

        self.batch1 = ProductBatch.objects.create(
            store=self.store1,
            product=self.prod_dona,
            quantity=Decimal("100.00"),
            purchase_price=Decimal("100000"),
            selling_price=Decimal("130000"),
        )
        self.batch2 = ProductBatch.objects.create(
            store=self.store2,
            product=self.prod_dona,
            quantity=Decimal("200.00"),
            purchase_price=Decimal("100000"),
            selling_price=Decimal("130000"),
        )
        self.batch3 = ProductBatch.objects.create(
            store=self.store3,
            product=self.prod_dona,
            quantity=Decimal("300.00"),
            purchase_price=Decimal("100000"),
            selling_price=Decimal("130000"),
        )

    def test_dona_multiple_stores_stock_updates(self):
        # 112-do'kon: 100 -> 105 (Import +5)
        # 115-do'kon: 200 -> 195 (Write-off -5)
        # 96-do'kon: 300 -> 300 (No adjustment)
        url = f"/api/products/{self.prod_dona.id}/update-stocks/"
        payload = {
            "stores": [
                {"store_id": self.store1.id, "new_quantity": 105, "min_stock": 20},
                {"store_id": self.store2.id, "new_quantity": 195, "min_stock": 10},
                {"store_id": self.store3.id, "new_quantity": 300, "min_stock": 15},
            ]
        }

        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 200)

        # Batch stocklar tekshiriladi
        self.batch1.refresh_from_db()
        self.batch2.refresh_from_db()
        self.batch3.refresh_from_db()
        self.assertEqual(self.batch1.quantity, Decimal("105"))
        self.assertEqual(self.batch2.quantity, Decimal("195"))
        self.assertEqual(self.batch3.quantity, Decimal("300"))

        # MinStock tekshiriladi
        self.assertEqual(self.batch1.min_stock, Decimal("20"))
        self.assertEqual(self.batch2.min_stock, Decimal("10"))
        self.assertEqual(self.batch3.min_stock, Decimal("15"))

        # Jurnaldagi yozuvlar tekshiriladi
        adjs = StockAdjustment.objects.filter(product=self.prod_dona).order_by("created_at")
        self.assertEqual(adjs.count(), 2)

        adj_import = adjs.filter(store=self.store1).first()
        self.assertIsNotNone(adj_import)
        self.assertEqual(adj_import.type, StockAdjustment.Type.IMPORT)
        self.assertEqual(adj_import.quantity, Decimal("5"))
        self.assertEqual(adj_import.purchase_price, Decimal("100000"))
        self.assertEqual(adj_import.total_amount, Decimal("500000"))

        adj_wo = adjs.filter(store=self.store2).first()
        self.assertIsNotNone(adj_wo)
        self.assertEqual(adj_wo.type, StockAdjustment.Type.WRITE_OFF)
        self.assertEqual(adj_wo.quantity, Decimal("5"))
        self.assertEqual(adj_wo.difference, Decimal("-5"))

        # 3-do'kondan hech qanday adjustment chiqmagan
        self.assertFalse(adjs.filter(store=self.store3).exists())

    def test_dona_invalid_step_atomic_rollback(self):
        # Store 1: 105 (OK)
        # Store 2: 195.5 (XATO - Dona uchun kasr qadam taqiqlangan)
        url = f"/api/products/{self.prod_dona.id}/update-stocks/"
        payload = {
            "stores": [
                {"store_id": self.store1.id, "new_quantity": 105},
                {"store_id": self.store2.id, "new_quantity": 195.5},
            ]
        }

        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 400)

        # Rollback tekshiruvi: Store 1 ham 100 ligicha qolgan
        self.batch1.refresh_from_db()
        self.batch2.refresh_from_db()
        self.assertEqual(self.batch1.quantity, Decimal("100"))
        self.assertEqual(self.batch2.quantity, Decimal("200"))
        self.assertEqual(StockAdjustment.objects.filter(product=self.prod_dona).count(), 0)

    def test_juft_fractional_steps_updates(self):
        # Juft mahsulot uchun 0.25 qadamlar
        b1 = ProductBatch.objects.create(
            store=self.store1,
            product=self.prod_juft,
            quantity=Decimal("100.00"),
            purchase_price=Decimal("50000"),
            selling_price=Decimal("70000"),
        )
        b2 = ProductBatch.objects.create(
            store=self.store2,
            product=self.prod_juft,
            quantity=Decimal("100.00"),
            purchase_price=Decimal("50000"),
            selling_price=Decimal("70000"),
        )

        url = f"/api/products/{self.prod_juft.id}/update-stocks/"
        payload = {
            "stores": [
                {"store_id": self.store1.id, "new_quantity": 100.25, "min_stock": 10.25},
                {"store_id": self.store2.id, "new_quantity": 99.75, "min_stock": 5.50},
            ]
        }

        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 200)

        b1.refresh_from_db()
        b2.refresh_from_db()
        self.assertEqual(b1.quantity, Decimal("100.25"))
        self.assertEqual(b2.quantity, Decimal("99.75"))
        self.assertEqual(b1.min_stock, Decimal("10.25"))
        self.assertEqual(b2.min_stock, Decimal("5.50"))

        adj1 = StockAdjustment.objects.get(product=self.prod_juft, store=self.store1)
        self.assertEqual(adj1.type, StockAdjustment.Type.IMPORT)
        self.assertEqual(adj1.quantity, Decimal("0.25"))

        adj2 = StockAdjustment.objects.get(product=self.prod_juft, store=self.store2)
        self.assertEqual(adj2.type, StockAdjustment.Type.WRITE_OFF)
        self.assertEqual(adj2.quantity, Decimal("0.25"))

    def test_min_stock_update_without_quantity_change(self):
        url = f"/api/products/{self.prod_dona.id}/update-stocks/"
        payload = {
            "stores": [
                {"store_id": self.store1.id, "min_stock": 30},
            ]
        }

        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 200)

        self.batch1.refresh_from_db()
        self.assertEqual(self.batch1.min_stock, Decimal("30"))
        # Quantity o'zgarmagan
        self.assertEqual(self.batch1.quantity, Decimal("100"))
        # Hech qanday adjustment yaratilmagan
        self.assertEqual(StockAdjustment.objects.filter(product=self.prod_dona).count(), 0)

