from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.inventory.models import LowStockItem
from apps.inventory.services import LowStockService
from apps.products.models import Product, ProductBatch
from apps.store.models import Store, StoreUser
from apps.transfer.models import Notification
from apps.users.models import User


class LowStockTestBase(TestCase):
    """Shared fixtures for low-stock tests."""

    _barcode_seq = 1000

    def make_product(self, name="P"):
        # Pass an explicit barcode so Product.save() skips barcode-image generation.
        LowStockTestBase._barcode_seq += 1
        return Product.objects.create(name=name, barcode=str(LowStockTestBase._barcode_seq))

    def make_store(self, store_type=Store.StoreType.BASE, name="S"):
        return Store.objects.create(
            name=name, phone_number="998900000000", address="addr", type=store_type
        )

    def make_batch(self, store, product, quantity, min_stock):
        return ProductBatch.objects.create(
            store=store,
            product=product,
            quantity=quantity,
            purchase_price=10,
            selling_price=20,
            min_stock=min_stock,
        )


class LowStockEvaluateTests(LowStockTestBase):

    def test_create_open_record_when_below_threshold(self):
        store = self.make_store(Store.StoreType.BASE)
        product = self.make_product()
        self.make_batch(store, product, quantity=3, min_stock=5)

        LowStockService.evaluate(store, product)

        items = LowStockItem.objects.filter(store=store, product=product)
        self.assertEqual(items.count(), 1)
        item = items.first()
        self.assertEqual(item.status, LowStockItem.Status.OPEN)
        self.assertEqual(item.current_quantity, 3)
        self.assertEqual(item.min_stock, 5)
        self.assertEqual(item.action_type, LowStockItem.ActionType.PURCHASE)

    def test_create_open_record_when_equal_to_threshold(self):
        store = self.make_store(Store.StoreType.BASE)
        product = self.make_product()
        self.make_batch(store, product, quantity=5, min_stock=5)

        LowStockService.evaluate(store, product)

        self.assertEqual(
            LowStockItem.objects.filter(status=LowStockItem.Status.OPEN).count(), 1
        )

    def test_min_stock_zero_disables_monitoring(self):
        store = self.make_store(Store.StoreType.BASE)
        product = self.make_product()
        self.make_batch(store, product, quantity=0, min_stock=0)

        LowStockService.evaluate(store, product)

        self.assertEqual(LowStockItem.objects.count(), 0)

    def test_no_record_when_above_threshold(self):
        store = self.make_store(Store.StoreType.BASE)
        product = self.make_product()
        self.make_batch(store, product, quantity=50, min_stock=5)

        LowStockService.evaluate(store, product)

        self.assertEqual(LowStockItem.objects.count(), 0)

    def test_prevent_duplicate_open_records_via_service(self):
        store = self.make_store(Store.StoreType.BASE)
        product = self.make_product()
        batch = self.make_batch(store, product, quantity=2, min_stock=5)

        LowStockService.evaluate(store, product)
        # Re-evaluate while still low — must NOT create a second OPEN record.
        LowStockService.evaluate(store, product)

        self.assertEqual(
            LowStockItem.objects.filter(
                store=store, product=product, status=LowStockItem.Status.OPEN
            ).count(),
            1,
        )

    def test_prevent_duplicate_open_records_via_db_constraint(self):
        store = self.make_store(Store.StoreType.BASE)
        product = self.make_product()

        LowStockItem.objects.create(
            store=store, product=product, current_quantity=1, min_stock=5,
            action_type=LowStockItem.ActionType.PURCHASE,
            status=LowStockItem.Status.OPEN,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LowStockItem.objects.create(
                    store=store, product=product, current_quantity=1, min_stock=5,
                    action_type=LowStockItem.ActionType.PURCHASE,
                    status=LowStockItem.Status.OPEN,
                )

    def test_resolve_record_when_stock_recovers(self):
        store = self.make_store(Store.StoreType.BASE)
        product = self.make_product()
        batch = self.make_batch(store, product, quantity=2, min_stock=5)

        LowStockService.evaluate(store, product)
        self.assertTrue(
            LowStockItem.objects.filter(status=LowStockItem.Status.OPEN).exists()
        )

        # Restock above threshold and re-evaluate.
        ProductBatch.objects.filter(id=batch.id).update(quantity=20)
        LowStockService.evaluate(store, product)

        self.assertEqual(
            LowStockItem.objects.filter(status=LowStockItem.Status.OPEN).count(), 0
        )
        resolved = LowStockItem.objects.get(status=LowStockItem.Status.RESOLVED)
        self.assertIsNotNone(resolved.resolved_at)

    def test_resolve_then_drop_creates_new_record(self):
        store = self.make_store(Store.StoreType.BASE)
        product = self.make_product()
        batch = self.make_batch(store, product, quantity=2, min_stock=5)

        LowStockService.evaluate(store, product)              # OPEN #1
        ProductBatch.objects.filter(id=batch.id).update(quantity=20)
        LowStockService.evaluate(store, product)              # RESOLVED #1
        ProductBatch.objects.filter(id=batch.id).update(quantity=1)
        LowStockService.evaluate(store, product)              # OPEN #2

        self.assertEqual(
            LowStockItem.objects.filter(status=LowStockItem.Status.OPEN).count(), 1
        )
        self.assertEqual(
            LowStockItem.objects.filter(status=LowStockItem.Status.RESOLVED).count(), 1
        )


class LowStockNotificationTests(LowStockTestBase):

    def _add_store_user(self, store, phone):
        user = User.objects.create(phone_number=phone, full_name="U")
        StoreUser.objects.create(
            user=user, store=store, role=StoreUser.Role.Manager, is_active=True
        )
        return user

    def test_notification_created_for_base_store_with_websocket(self):
        store = self.make_store(Store.StoreType.BASE)
        self._add_store_user(store, "998900000001")
        product = self.make_product()
        self.make_batch(store, product, quantity=1, min_stock=5)

        with mock.patch(
            "apps.inventory.services.low_stock_service.get_channel_layer"
        ) as gcl, mock.patch(
            "apps.inventory.services.low_stock_service.async_to_sync"
        ) as a2s:
            gcl.return_value = mock.MagicMock()
            a2s.return_value = mock.MagicMock()
            with self.captureOnCommitCallbacks(execute=True):
                LowStockService.evaluate(store, product)

        notif = Notification.objects.get()
        self.assertEqual(notif.type, Notification.Type.LOW_STOCK_PURCHASE)
        # BASE -> realtime websocket attempted.
        self.assertTrue(a2s.called)

    def test_notification_created_for_store_without_websocket(self):
        store = self.make_store(Store.StoreType.STORE)
        self._add_store_user(store, "998900000002")
        product = self.make_product()
        self.make_batch(store, product, quantity=1, min_stock=5)

        with mock.patch(
            "apps.inventory.services.low_stock_service.get_channel_layer"
        ) as gcl, mock.patch(
            "apps.inventory.services.low_stock_service.async_to_sync"
        ) as a2s:
            gcl.return_value = mock.MagicMock()
            a2s.return_value = mock.MagicMock()
            with self.captureOnCommitCallbacks(execute=True):
                LowStockService.evaluate(store, product)

        notif = Notification.objects.get()
        self.assertEqual(notif.type, Notification.Type.LOW_STOCK_TRANSFER)
        # STORE -> NO realtime websocket.
        self.assertFalse(a2s.called)

        item = LowStockItem.objects.get()
        self.assertEqual(item.action_type, LowStockItem.ActionType.TRANSFER)

    def test_notification_sent_only_once_while_below_threshold(self):
        store = self.make_store(Store.StoreType.BASE)
        self._add_store_user(store, "998900000003")
        product = self.make_product()
        self.make_batch(store, product, quantity=1, min_stock=5)

        with self.captureOnCommitCallbacks(execute=True):
            LowStockService.evaluate(store, product)
        with self.captureOnCommitCallbacks(execute=True):
            LowStockService.evaluate(store, product)  # still low, no new OPEN/notif

        self.assertEqual(Notification.objects.count(), 1)
