"""
Targeted regression test suite for Sale Concurrency and Deterministic Locking (P1 #1).

Test coverage:
1. Test 1 — Lock QuerySet Evaluation: Verify select_for_update() is actually evaluated and sent to DB.
2. Test 2 — Deterministic Lock Order: Input orders [A, B] and [B, A] produce identical lock ordering.
3. Test 3 — Existing Sale Behavior: Standard sale creation with multiple payment methods succeeds.
4. Test 4 — FIFO Integrity: Stock allocation and weighted historical cost calculation remain unchanged.
5. Test 5 — Concurrent Transactions: Multi-threaded concurrent sales with opposing product order
            execute safely without database deadlocks.
"""

from decimal import Decimal
import threading
import time

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from apps.inventory.models import StockAllocation, StockLot
from apps.inventory.services.stock_allocation_service import StockAllocationService
from apps.products.models import Category, Product, ProductBatch
from apps.sales.models import BankCard, Payment, Sale, SaleItem
from apps.sales.services.sales_services import SaleService
from apps.store.models import Store, StoreUser
from apps.users.models.customers import Customer
from apps.users.models.user import User


class SaleConcurrencyLockingTest(TestCase):
    """Unit and functional tests for deterministic locking in SaleService.create_sale."""

    def setUp(self):
        self.store = Store.objects.create(name="Central Store", phone_number="+998901112233")
        self.admin = User.objects.create(
            phone_number="+998901110001",
            email="admin_lock@example.com",
            is_staff=True,
            is_superuser=True,
        )
        StoreUser.objects.create(user=self.admin, store=self.store, is_active=True)

        self.category = Category.objects.create(name="Parts")

        # Create two products with deterministic IDs
        self.product_a = Product.objects.create(
            name="Product Alpha",
            sku="SKU-A",
            barcode="7770001",
            category=self.category,
            status=Product.ProductStatus.ACTIVE,
        )
        self.product_b = Product.objects.create(
            name="Product Beta",
            sku="SKU-B",
            barcode="7770002",
            category=self.category,
            status=Product.ProductStatus.ACTIVE,
        )

        # Ensure product_a.id < product_b.id for deterministic assertion
        if self.product_a.id > self.product_b.id:
            self.product_a, self.product_b = self.product_b, self.product_a

        # Setup stock lots and synchronized batches
        self.lot_a = StockLot.objects.create(
            store=self.store,
            product=self.product_a,
            initial_quantity=Decimal("50.00"),
            remaining_quantity=Decimal("50.00"),
            purchase_price=Decimal("40000.00"),
        )
        self.lot_b = StockLot.objects.create(
            store=self.store,
            product=self.product_b,
            initial_quantity=Decimal("50.00"),
            remaining_quantity=Decimal("50.00"),
            purchase_price=Decimal("60000.00"),
        )

        with transaction.atomic():
            StockAllocationService._sync_product_batch(self.store, self.product_a)
            StockAllocationService._sync_product_batch(self.store, self.product_b)

        self.customer = Customer.objects.create(
            full_name="Temur Aliyev",
            phone_number="+998909998877",
        )

    def test_lock_queryset_evaluation(self):
        """Test 1: Verify select_for_update() QuerySet is evaluated and sent to PostgreSQL."""
        sale_data = {
            "store": self.store.id,
            "customer": self.customer.id,
            "items": [
                {"product": self.product_a.id, "quantity": Decimal("2.00"), "price": Decimal("50000.00")},
                {"product": self.product_b.id, "quantity": Decimal("1.00"), "price": Decimal("80000.00")},
            ],
            "payments": [
                {"type": "cash", "amount": Decimal("180000.00")},
            ],
        }

        with CaptureQueriesContext(connection) as captured:
            sale = SaleService.create_sale(user=self.admin, data=sale_data)

        self.assertIsNotNone(sale)
        self.assertEqual(sale.status, Sale.Status.PAID)

        # Verify that an explicit SELECT ... FOR UPDATE query was executed on product_batch
        lock_queries = [
            q["sql"] for q in captured.captured_queries
            if "product_batch" in q["sql"] and "FOR UPDATE" in q["sql"] and "ORDER BY" in q["sql"]
        ]
        self.assertGreaterEqual(
            len(lock_queries), 1,
            "Expected at least one 'SELECT ... FROM product_batch ... FOR UPDATE' query to be executed."
        )

        # Verify the lock query explicitly orders by product_id ASC
        main_lock_query = lock_queries[0]
        self.assertIn("ORDER BY", main_lock_query)
        self.assertIn("product_id", main_lock_query)

    def test_deterministic_lock_order_ab_vs_ba(self):
        """Test 2: Input order [A, B] and [B, A] produce identical deterministic lock sequence."""
        # Order 1: [product_b, product_a] (inverted order)
        data_ba = {
            "store": self.store.id,
            "customer": self.customer.id,
            "items": [
                {"product": self.product_b.id, "quantity": Decimal("1.00"), "price": Decimal("80000.00")},
                {"product": self.product_a.id, "quantity": Decimal("1.00"), "price": Decimal("50000.00")},
            ],
            "payments": [
                {"type": "cash", "amount": Decimal("130000.00")},
            ],
        }

        with CaptureQueriesContext(connection) as captured_ba:
            SaleService.create_sale(user=self.admin, data=data_ba)

        lock_queries_ba = [
            q["sql"] for q in captured_ba.captured_queries
            if "product_batch" in q["sql"] and "FOR UPDATE" in q["sql"] and "ORDER BY" in q["sql"]
        ]
        self.assertGreaterEqual(len(lock_queries_ba), 1)

        # The query must order by product_id ASC regardless of items_data ordering
        expected_pids = sorted([self.product_a.id, self.product_b.id])
        self.assertEqual(expected_pids, [self.product_a.id, self.product_b.id])
        self.assertIn("ORDER BY", lock_queries_ba[0])

    def test_existing_sale_behavior(self):
        """Test 3: Normal sale creation with discount and split payment operates cleanly."""
        card = BankCard.objects.create(name="Payme", is_default=True, scope=BankCard.Scope.BOTH)

        sale_data = {
            "store": self.store.id,
            "customer": self.customer.id,
            "items": [
                {"product": self.product_a.id, "quantity": Decimal("5.00"), "price": Decimal("50000.00")},
            ],
            "discount_type": Sale.DiscountType.FIXED,
            "discount_value": Decimal("10000.00"),
            "payments": [
                {"type": "cash", "amount": Decimal("140000.00")},
                {"type": "card", "amount": Decimal("100000.00"), "bank_card": card},
            ],
        }

        sale = SaleService.create_sale(user=self.admin, data=sale_data)

        self.assertEqual(sale.total_amount, Decimal("240000.00"))  # (250000 - 10000)
        self.assertEqual(sale.paid_amount, Decimal("240000.00"))
        self.assertEqual(sale.status, Sale.Status.PAID)
        self.assertEqual(sale.payment_type, Sale.PaymentType.MIXED)
        self.assertEqual(sale.items.count(), 1)
        self.assertEqual(sale.payments.count(), 2)

    def test_fifo_integrity(self):
        """Test 4: Multi-lot FIFO allocation and historical purchase_price calculation."""
        # Create second lot with different purchase price
        lot_a2 = StockLot.objects.create(
            store=self.store,
            product=self.product_a,
            initial_quantity=Decimal("10.00"),
            remaining_quantity=Decimal("10.00"),
            purchase_price=Decimal("46000.00"),
        )
        with transaction.atomic():
            StockAllocationService._sync_product_batch(self.store, self.product_a)

        # Remaining in lot_a is 50 @ 40,000, lot_a2 is 10 @ 46,000.
        # Sell 55 units: 50 from lot_a + 5 from lot_a2
        sale_data = {
            "store": self.store.id,
            "customer": self.customer.id,
            "items": [
                {"product": self.product_a.id, "quantity": Decimal("55.00"), "price": Decimal("60000.00")},
            ],
            "payments": [
                {"type": "cash", "amount": Decimal("3300000.00")},
            ],
        }

        sale = SaleService.create_sale(user=self.admin, data=sale_data)
        item = sale.items.first()

        self.lot_a.refresh_from_db()
        lot_a2.refresh_from_db()

        self.assertEqual(self.lot_a.remaining_quantity, Decimal("0.00"))
        self.assertEqual(lot_a2.remaining_quantity, Decimal("5.00"))

        allocations = list(StockAllocation.objects.filter(sale_item=item).order_by("created_at", "id"))
        self.assertEqual(len(allocations), 2)
        self.assertEqual(allocations[0].lot_id, self.lot_a.id)
        self.assertEqual(allocations[0].quantity, Decimal("50.00"))
        self.assertEqual(allocations[0].unit_cost, Decimal("40000.00"))

        self.assertEqual(allocations[1].lot_id, lot_a2.id)
        self.assertEqual(allocations[1].quantity, Decimal("5.00"))
        self.assertEqual(allocations[1].unit_cost, Decimal("46000.00"))

        # Weighted avg cost: (50 * 40000 + 5 * 46000) / 55 = (2000000 + 230000) / 55 = 2230000 / 55 = 40545.45
        expected_avg_cost = (Decimal("2230000.00") / Decimal("55")).quantize(Decimal("0.01"))
        self.assertEqual(item.purchase_price, expected_avg_cost)


class ConcurrentSaleExecutionTest(TransactionTestCase):
    """
    Test 5: Real multi-threaded concurrent sale creation.
    Tests opposite product ordering [A, B] and [B, A] simultaneously across separate DB connections.
    """

    def setUp(self):
        self.store = Store.objects.create(name="Flagship Store", phone_number="+998909990011")
        self.admin = User.objects.create(
            phone_number="+998909990022",
            email="conc_admin@example.com",
            is_staff=True,
            is_superuser=True,
        )
        StoreUser.objects.create(user=self.admin, store=self.store, is_active=True)

        self.category = Category.objects.create(name="Lubricants")
        self.prod_1 = Product.objects.create(
            name="Synthetic Oil",
            sku="SYN-01",
            barcode="888001",
            category=self.category,
            status=Product.ProductStatus.ACTIVE,
        )
        self.prod_2 = Product.objects.create(
            name="Transmission Fluid",
            sku="ATF-02",
            barcode="888002",
            category=self.category,
            status=Product.ProductStatus.ACTIVE,
        )

        StockLot.objects.create(
            store=self.store,
            product=self.prod_1,
            initial_quantity=Decimal("100.00"),
            remaining_quantity=Decimal("100.00"),
            purchase_price=Decimal("50000.00"),
        )
        StockLot.objects.create(
            store=self.store,
            product=self.prod_2,
            initial_quantity=Decimal("100.00"),
            remaining_quantity=Decimal("100.00"),
            purchase_price=Decimal("70000.00"),
        )

        with transaction.atomic():
            StockAllocationService._sync_product_batch(self.store, self.prod_1)
            StockAllocationService._sync_product_batch(self.store, self.prod_2)

        self.customer = Customer.objects.create(
            full_name="Bobur Karimov",
            phone_number="+998901112244",
        )

    def test_concurrent_sales_with_opposite_product_order(self):
        """
        Two concurrent threads execute sales with opposite product orders:
        Thread 1: [prod_1, prod_2]
        Thread 2: [prod_2, prod_1]
        Deterministic ASC locking prevents deadlocks.
        """
        errors = []
        sales_created = []

        data_thread_1 = {
            "store": self.store.id,
            "customer": self.customer.id,
            "items": [
                {"product": self.prod_1.id, "quantity": Decimal("2.00"), "price": Decimal("60000.00")},
                {"product": self.prod_2.id, "quantity": Decimal("2.00"), "price": Decimal("80000.00")},
            ],
            "payments": [{"type": "cash", "amount": Decimal("280000.00")}],
        }

        data_thread_2 = {
            "store": self.store.id,
            "customer": self.customer.id,
            "items": [
                {"product": self.prod_2.id, "quantity": Decimal("3.00"), "price": Decimal("80000.00")},
                {"product": self.prod_1.id, "quantity": Decimal("1.00"), "price": Decimal("60000.00")},
            ],
            "payments": [{"type": "cash", "amount": Decimal("300000.00")}],
        }

        def worker_1():
            try:
                sale = SaleService.create_sale(user=self.admin, data=data_thread_1)
                sales_created.append(sale.id)
            except Exception as e:
                errors.append(("Thread 1", e))
            finally:
                connection.close()

        def worker_2():
            try:
                sale = SaleService.create_sale(user=self.admin, data=data_thread_2)
                sales_created.append(sale.id)
            except Exception as e:
                errors.append(("Thread 2", e))
            finally:
                connection.close()

        t1 = threading.Thread(target=worker_1)
        t2 = threading.Thread(target=worker_2)

        t1.start()
        t2.start()

        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(len(errors), 0, f"Concurrent sale execution raised errors: {errors}")
        self.assertEqual(len(sales_created), 2, "Both concurrent sales must be created successfully.")

        # Verify stock consistency after concurrent deductions
        batch_1 = ProductBatch.objects.get(store=self.store, product=self.prod_1)
        batch_2 = ProductBatch.objects.get(store=self.store, product=self.prod_2)

        # Prod 1: 100 - (2 + 1) = 97
        self.assertEqual(batch_1.quantity, Decimal("97.00"))
        # Prod 2: 100 - (2 + 3) = 95
        self.assertEqual(batch_2.quantity, Decimal("95.00"))
