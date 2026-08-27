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


class ProductHistoryMovementAndRollbackTests(TestCase):
    """Mahsulot tarixi + Stock movements jurnallashtirish + Reversal testlari."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(
            phone_number="+998901112233",
            full_name="Audit Admin",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_authenticate(user=self.user)

        self.store = Store.objects.create(name="112-do'kon", is_active=True)
        StoreUser.objects.create(user=self.user, store=self.store)

        self.unit_dona = ProductUnitMeasurement.objects.create(
            measurement="dona",
            quantity_type=ProductUnitMeasurement.QuantityType.WHOLE,
        )
        self.unit_juft = ProductUnitMeasurement.objects.create(
            measurement="пара",
            quantity_type=ProductUnitMeasurement.QuantityType.QUARTER,
        )

        self.prod_dona = Product.objects.create(
            name="Universal zajim",
            sku="ZAJ-001",
            unit_measurement=self.unit_dona,
            min_stock=Decimal("10"),
            status=Product.ProductStatus.ACTIVE,
        )
        self.prod_juft = Product.objects.create(
            name="Fara juft",
            sku="FAR-001",
            unit_measurement=self.unit_juft,
            min_stock=Decimal("5"),
            status=Product.ProductStatus.ACTIVE,
        )

        self.batch_dona = ProductBatch.objects.create(
            store=self.store,
            product=self.prod_dona,
            quantity=Decimal("480.00"),
            purchase_price=Decimal("50000.00"),
            selling_price=Decimal("75000.00"),
        )
        self.batch_juft = ProductBatch.objects.create(
            store=self.store,
            product=self.prod_juft,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("200000.00"),
            selling_price=Decimal("280000.00"),
        )

    def test_dona_stock_movements_in_history(self):
        from apps.inventory.services.stock_adjustment_service import StockAdjustmentService
        from apps.products.services.product_history_service import ProductHistoryService

        # 1. 480 -> 485 (+5 Import)
        adj_imp = StockAdjustmentService.create_adjustment(
            store_id=self.store.id,
            product_id=self.prod_dona.id,
            quantity=Decimal("5"),
            type=StockAdjustment.Type.IMPORT,
            reason=StockAdjustment.Reason.RECOUNT,
            comment="Qayta sanash natijasida kirim",
            user=self.user,
        )

        # 2. 485 -> 475 (-10 Write-off)
        adj_wo = StockAdjustmentService.create_adjustment(
            store_id=self.store.id,
            product_id=self.prod_dona.id,
            quantity=Decimal("10"),
            type=StockAdjustment.Type.WRITE_OFF,
            reason=StockAdjustment.Reason.DAMAGED,
            comment="Buzilgan qism",
            user=self.user,
        )

        self.batch_dona.refresh_from_db()
        self.assertEqual(self.batch_dona.quantity, Decimal("475"))

        # ProductHistory tekshiruvi
        service = ProductHistoryService(self.prod_dona, self.user)
        history = service.build()
        events = history["events"]["results"]

        # 2 ta movement topilishi kerak
        event_types = [e["type"] for e in events]
        self.assertIn("import", event_types)
        self.assertIn("writeoff", event_types)

        imp_ev = next(e for e in events if e["doc_id"] == adj_imp.id and e["type"] == "import")
        self.assertEqual(imp_ev["quantity"], Decimal("5"))
        self.assertEqual(imp_ev["old_quantity"], Decimal("480"))
        self.assertEqual(imp_ev["new_quantity"], Decimal("485"))
        self.assertEqual(imp_ev["price"], Decimal("50000.00"))
        self.assertEqual(imp_ev["amount"], Decimal("250000.00"))
        self.assertEqual(imp_ev["user"], "Audit Admin")
        self.assertEqual(imp_ev["status"], "active")

        wo_ev = next(e for e in events if e["doc_id"] == adj_wo.id and e["type"] == "writeoff")
        self.assertEqual(wo_ev["quantity"], Decimal("-10"))
        self.assertEqual(wo_ev["old_quantity"], Decimal("485"))
        self.assertEqual(wo_ev["new_quantity"], Decimal("475"))
        self.assertEqual(wo_ev["status"], "active")

    def test_juft_stock_movements_in_history(self):
        from apps.inventory.services.stock_adjustment_service import StockAdjustmentService
        from apps.products.services.product_history_service import ProductHistoryService

        # 10.00 -> 10.25 (+0.25 Import)
        StockAdjustmentService.create_adjustment(
            store_id=self.store.id,
            product_id=self.prod_juft.id,
            quantity=Decimal("0.25"),
            type=StockAdjustment.Type.IMPORT,
            user=self.user,
        )
        # 10.25 -> 9.50 (-0.75 Write-off)
        StockAdjustmentService.create_adjustment(
            store_id=self.store.id,
            product_id=self.prod_juft.id,
            quantity=Decimal("0.75"),
            type=StockAdjustment.Type.WRITE_OFF,
            user=self.user,
        )

        self.batch_juft.refresh_from_db()
        self.assertEqual(self.batch_juft.quantity, Decimal("9.50"))

        service = ProductHistoryService(self.prod_juft, self.user)
        history = service.build()
        events = history["events"]["results"]

        imp_ev = next(e for e in events if e["type"] == "import")
        self.assertEqual(imp_ev["quantity"], Decimal("0.25"))

        wo_ev = next(e for e in events if e["type"] == "writeoff")
        self.assertEqual(wo_ev["quantity"], Decimal("-0.75"))

    def test_rollback_a_then_b_safety(self):
        from apps.inventory.services.stock_adjustment_service import StockAdjustmentService
        from apps.products.services.product_history_service import ProductHistoryService

        # 480 -> 485 (+5 Import A)
        adj_a = StockAdjustmentService.create_adjustment(
            store_id=self.store.id,
            product_id=self.prod_dona.id,
            quantity=Decimal("5"),
            type=StockAdjustment.Type.IMPORT,
            user=self.user,
        )
        # 485 -> 490 (+5 Import B)
        adj_b = StockAdjustmentService.create_adjustment(
            store_id=self.store.id,
            product_id=self.prod_dona.id,
            quantity=Decimal("5"),
            type=StockAdjustment.Type.IMPORT,
            user=self.user,
        )

        self.batch_dona.refresh_from_db()
        self.assertEqual(self.batch_dona.quantity, Decimal("490"))

        # Import A rollback qilinadi (490 - 5 = 485 bo'lishi kerak, 480 EMAS!)
        StockAdjustmentService.cancel_adjustment(adjustment_id=adj_a.id, user=self.user)

        self.batch_dona.refresh_from_db()
        self.assertEqual(self.batch_dona.quantity, Decimal("485"))

        adj_a.refresh_from_db()
        self.assertEqual(adj_a.status, StockAdjustment.Status.CANCELLED)
        self.assertEqual(adj_a.cancelled_by, self.user)
        self.assertIsNotNone(adj_a.cancelled_at)

        # Tarixda bekor qilingan statusi ko'rinishi
        service = ProductHistoryService(self.prod_dona, self.user)
        events = service.build()["events"]["results"]
        ev_a = next(e for e in events if e["doc_id"] == adj_a.id)
        self.assertEqual(ev_a["status"], "cancelled")
        self.assertEqual(ev_a["cancelled_by"], "Audit Admin")

    def test_duplicate_rollback_protection(self):
        from apps.inventory.services.stock_adjustment_service import StockAdjustmentService
        from rest_framework.exceptions import ValidationError

        adj = StockAdjustmentService.create_adjustment(
            store_id=self.store.id,
            product_id=self.prod_dona.id,
            quantity=Decimal("5"),
            type=StockAdjustment.Type.IMPORT,
            user=self.user,
        )
        StockAdjustmentService.cancel_adjustment(adjustment_id=adj.id, user=self.user)
        self.batch_dona.refresh_from_db()
        self.assertEqual(self.batch_dona.quantity, Decimal("480"))

        # Ikkinchi marta bekor qilish rad etiladi va miqdor o'zgarmaydi
        with self.assertRaises(ValidationError):
            StockAdjustmentService.cancel_adjustment(adjustment_id=adj.id, user=self.user)

        self.batch_dona.refresh_from_db()
        self.assertEqual(self.batch_dona.quantity, Decimal("480"))

    def test_rollback_insufficient_stock_fails_safely(self):
        from apps.inventory.services.stock_adjustment_service import StockAdjustmentService
        from rest_framework.exceptions import ValidationError

        # 480 -> 485 (+5 Import)
        adj = StockAdjustmentService.create_adjustment(
            store_id=self.store.id,
            product_id=self.prod_dona.id,
            quantity=Decimal("5"),
            type=StockAdjustment.Type.IMPORT,
            user=self.user,
        )
        # Ombordagi qoldiq boshqa amallar tufayli 2 ga tushib qolgan bo'lsin
        self.batch_dona.quantity = Decimal("2")
        self.batch_dona.save(update_fields=["quantity"])

        # +5 lik importni bekor qilish rad etiladi (chunki 2 - 5 = -3 bo'lib ketishi mumkin emas)
        with self.assertRaises(ValidationError):
            StockAdjustmentService.cancel_adjustment(adjustment_id=adj.id, user=self.user)

        self.batch_dona.refresh_from_db()
        self.assertEqual(self.batch_dona.quantity, Decimal("2"))
        adj.refresh_from_db()
        self.assertEqual(adj.status, StockAdjustment.Status.ACTIVE)

    def test_product_history_api_endpoint(self):
        url = f"/api/products/{self.prod_dona.id}/history/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("summary", response.data)
        self.assertIn("by_store", response.data)
        self.assertIn("events", response.data)

    def test_sale_and_transfer_do_not_appear_in_product_history(self):
        from apps.sales.models import Sale, SaleItem
        from apps.transfer.models import StockTransfer, StockTransferItem
        from apps.products.services.product_history_service import ProductHistoryService

        # 1. Sotuv yaratamiz
        sale = Sale.objects.create(
            store=self.store,
            seller=self.user,
            total_amount=Decimal("100"),
            paid_amount=Decimal("100"),
            status=Sale.Status.PAID,
        )
        SaleItem.objects.create(
            sale=sale,
            product=self.prod_dona,
            quantity=Decimal("2"),
            unit_price=Decimal("50"),
            total_price=Decimal("100"),
        )

        # 2. O'tkazma yaratamiz
        transfer = StockTransfer.objects.create(
            from_store=self.store,
            to_store=Store.objects.create(name="Filial 2", is_active=True),
            status=StockTransfer.Status.APPROVED,
            created_by=self.user,
        )
        StockTransferItem.objects.create(
            stock_transfer=transfer,
            product=self.prod_dona,
            quantity=Decimal("3"),
            purchase_price=Decimal("50"),
            selling_price=Decimal("100"),
        )

        # 3. ProductHistoryService da sale yoki transfer mutlaqo chiqmasligi kerak
        service = ProductHistoryService(self.prod_dona, self.user)
        built = service.build()
        event_types = [e["type"] for e in built["events"]["results"]]

        self.assertNotIn("sale", event_types)
        self.assertNotIn("transfer", event_types)
        self.assertNotIn("sale_return", event_types)
        self.assertEqual(built["events"]["count"], 0)

    def test_product_field_history_tracking(self):
        from apps.products.serializers.product_crud_serializer import ProductUpdateSerializer
        from apps.products.services.product_history_service import ProductHistoryService
        from apps.products.models import ProductFieldHistory

        # Mahsulot ma'lumotlarini serializer orqali o'zgartiramiz
        serializer = ProductUpdateSerializer(
            instance=self.prod_dona,
            data={
                "name": "Yangi Universal zajim",
                "sku": "SKU-9999",
                "min_stock": 25,
            },
            partial=True,
            context={"request": type("Request", (), {"user": self.user})()},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        # ProductFieldHistory da yozuvlar yaratilganligini tekshiramiz
        histories = ProductFieldHistory.objects.filter(product=self.prod_dona)
        self.assertTrue(histories.exists())
        field_names = {h.field_name for h in histories}
        self.assertIn("name", field_names)
        self.assertIn("sku", field_names)
        self.assertIn("min_stock", field_names)

        # ProductHistoryService da field_change hodisalari to'g'ri chiqishini tekshiramiz
        service = ProductHistoryService(self.prod_dona, self.user)
        built = service.build()
        field_events = [e for e in built["events"]["results"] if e["type"] == "field_change"]
        self.assertEqual(len(field_events), 3)

        name_event = next(e for e in field_events if e["field_name"] == "name")
        self.assertEqual(name_event["old_value"], "Universal zajim")
        self.assertEqual(name_event["new_value"], "Yangi Universal zajim")
        self.assertEqual(name_event["user"], "Audit Admin")

    def test_inventory_adjustment_appears_in_product_history(self):
        from apps.inventory.models import InventorySession, InventoryAdjustment
        from apps.products.services.product_history_service import ProductHistoryService

        session = InventorySession.objects.create(
            store=self.store,
            started_by=self.user,
            status=InventorySession.Status.COMPLETED,
        )
        InventoryAdjustment.objects.create(
            session=session,
            product=self.prod_dona,
            difference=Decimal("5.00"),
        )

        service = ProductHistoryService(self.prod_dona, self.user)
        built = service.build()
        inv_events = [e for e in built["events"]["results"] if e["type"] == "inventory"]
        self.assertEqual(len(inv_events), 1)
        self.assertEqual(inv_events[0]["quantity"], Decimal("5.00"))
        self.assertEqual(inv_events[0]["doc_id"], session.id)

    def test_store_isolation_in_product_history(self):
        from apps.inventory.services.stock_adjustment_service import StockAdjustmentService
        from apps.products.services.product_history_service import ProductHistoryService

        store_b = Store.objects.create(name="115-do'kon", is_active=True)
        StoreUser.objects.create(user=self.user, store=store_b)
        ProductBatch.objects.create(
            store=store_b,
            product=self.prod_dona,
            quantity=Decimal("100.00"),
            purchase_price=Decimal("50000.00"),
            selling_price=Decimal("75000.00"),
        )

        # Store A: +5
        StockAdjustmentService.create_adjustment(
            store_id=self.store.id,
            product_id=self.prod_dona.id,
            quantity=Decimal("5"),
            type=StockAdjustment.Type.IMPORT,
            user=self.user,
        )
        # Store B: +10
        StockAdjustmentService.create_adjustment(
            store_id=store_b.id,
            product_id=self.prod_dona.id,
            quantity=Decimal("10"),
            type=StockAdjustment.Type.IMPORT,
            user=self.user,
        )

        # Store A filtri bilan so'rov
        service_a = ProductHistoryService(self.prod_dona, self.user, store_id=self.store.id)
        built_a = service_a.build()
        events_a = built_a["events"]["results"]
        self.assertEqual(len(events_a), 1)
        self.assertEqual(events_a[0]["store_id"], self.store.id)
        self.assertEqual(events_a[0]["quantity"], Decimal("5"))



