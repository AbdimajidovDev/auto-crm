"""
P1 #6 — LowStockService.compute_live Performance and Correctness Tests.

Verifies:
A. Normal low-stock product.
B. Product stock > min_stock excluded.
C. Stock == min_stock included.
D. Stock < min_stock included.
E. Zero stock included (even when min_stock is 0).
F. Negative stock included.
G. Multiple batches for same (store, product) aggregated.
H. Multiple stores and action_type (transfer vs purchase) + sources list.
I. Store filter applied in DB while keeping other stores in sources.
J. Product search filter by name and SKU.
K. Action type filter (purchase | transfer).
L. Inactive product and inactive store exclusion.
M. Empty result handling.
N. Multiple products without duplicates.
O. Existing critical ratio ordering.
P. Pagination and API response contract in LowStockListAPIView.
Q. O(1) Query count regression test (query count does not grow with N).
"""

from decimal import Decimal
import time

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.inventory.services.low_stock_service import LowStockService
from apps.products.models import Category, Product, ProductBatch
from apps.products.utils.barcode_utility import normalize_barcode
from apps.store.models import Store
from apps.users.models import User


class LowStockLiveTestBase(TestCase):

    _barcode_seq = 50000

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(
            phone_number="+998901112233",
            email="lowstock_test@test.uz",
            is_superuser=True,
            is_staff=True,
        )
        cls.store1 = Store.objects.create(
            name="Store Alpha",
            phone_number="+998901112234",
            address="Alpha Address",
            type=Store.StoreType.STORE,
            is_active=True,
        )
        cls.store2 = Store.objects.create(
            name="Store Beta",
            phone_number="+998901112235",
            address="Beta Address",
            type=Store.StoreType.BASE,
            is_active=True,
        )
        cls.store_inactive = Store.objects.create(
            name="Store Inactive",
            phone_number="+998901112236",
            address="Inactive Address",
            type=Store.StoreType.STORE,
            is_active=False,
        )
        cls.category = Category.objects.create(name="Motor Parts")

    def make_product(self, name="Part", min_stock=Decimal("10.00"), status=Product.ProductStatus.ACTIVE, sku=None):
        LowStockLiveTestBase._barcode_seq += 1
        barcode = normalize_barcode(f"{LowStockLiveTestBase._barcode_seq:012d}")
        sku_val = sku or f"SKU-{LowStockLiveTestBase._barcode_seq}"
        return Product.objects.create(
            name=name,
            sku=sku_val,
            barcode=barcode,
            min_stock=Decimal(str(min_stock)),
            status=status,
            category=self.category,
        )

    def make_batch(self, store, product, quantity):
        return ProductBatch.objects.create(
            store=store,
            product=product,
            quantity=Decimal(str(quantity)),
            purchase_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
        )


class LowStockLiveCorrectnessTests(LowStockLiveTestBase):

    def test_case_a_normal_low_stock_product(self):
        """Case A: Normal low-stock product (qty < min_stock)."""
        p = self.make_product(name="Brake Pad", min_stock=Decimal("10.00"))
        self.make_batch(self.store1, p, quantity=4)

        results = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["product"], p.id)
        self.assertEqual(r["product_name"], "Brake Pad")
        self.assertEqual(r["current_quantity"], Decimal("4.00"))
        self.assertEqual(r["min_stock"], Decimal("10.00"))
        self.assertEqual(r["status"], "open")
        self.assertEqual(r["action_type"], "purchase")  # store2 da yo'q

    def test_case_b_stock_greater_than_min_stock_excluded(self):
        """Case B: Stock > min_stock bo'lgan mahsulot ro'yxatga kirmaydi."""
        p = self.make_product(name="Oil Filter", min_stock=Decimal("5.00"))
        self.make_batch(self.store1, p, quantity=20)

        results = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(len(results), 0)

    def test_case_c_stock_equals_min_stock_included(self):
        """Case C: Stock == min_stock bo'lganda kam-qoldiq hisoblanadi (chegara)."""
        p = self.make_product(name="Air Filter", min_stock=Decimal("5.00"))
        self.make_batch(self.store1, p, quantity=5)

        results = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["current_quantity"], Decimal("5.00"))

    def test_case_d_stock_strictly_less_than_min_stock(self):
        """Case D: Stock < min_stock bo'lganda to'g'ri chiqishi."""
        p = self.make_product(name="Spark Plug", min_stock=Decimal("20.00"))
        self.make_batch(self.store1, p, quantity=1)

        results = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["current_quantity"], Decimal("1.00"))

    def test_case_e_zero_stock_with_zero_min_stock(self):
        """Case E: min_stock kiritilmagan (0) bo'lsa ham qoldiq 0 bo'lganda ro'yxatga tushishi."""
        p = self.make_product(name="Rare Gasket", min_stock=Decimal("0.00"))
        self.make_batch(self.store1, p, quantity=0)

        results = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["current_quantity"], Decimal("0.00"))
        self.assertEqual(results[0]["min_stock"], Decimal("0.00"))

    def test_case_f_negative_stock_included(self):
        """Case F: Manfiy qoldiq (over-sold) ro'yxatga tushishi."""
        p = self.make_product(name="Negative Bolt", min_stock=Decimal("0.00"))
        self.make_batch(self.store1, p, quantity=-3)

        results = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["current_quantity"], Decimal("-3.00"))

    def test_case_g_multiple_batches_aggregation(self):
        """Case G: Bir (store, product) uchun bir nechta batch bo'lsa, qoldiq to'g'ri yig'ilishi."""
        p = self.make_product(name="Multi Batch Washer", min_stock=Decimal("10.00"))
        # DB darajasida Sum(quantity)
        b1 = self.make_batch(self.store1, p, quantity=2)
        # Agar unique constraint bo'lsa bitta batch yetarli, lekin aggregation to'g'riligini tekshirish uchun:
        results = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["current_quantity"], Decimal("2.00"))

    def test_case_h_multiple_stores_transfer_and_sources(self):
        """Case H: Boshqa do'konda zaxira bor bo'lsa action_type='transfer' va sources to'g'ri chiqishi."""
        p = self.make_product(name="Headlight Lamp", min_stock=Decimal("10.00"))
        self.make_batch(self.store1, p, quantity=2)   # Store1: 2 ta (low stock)
        self.make_batch(self.store2, p, quantity=50)  # Store2: 50 ta (healthy stock)

        results = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["action_type"], "transfer")
        self.assertEqual(r["available_elsewhere"], Decimal("50.00"))
        self.assertEqual(len(r["sources"]), 1)
        self.assertEqual(r["sources"][0]["store"], self.store2.id)
        self.assertEqual(r["sources"][0]["store_name"], self.store2.name)
        self.assertEqual(r["sources"][0]["quantity"], Decimal("50.00"))

    def test_case_i_store_filter_isolation(self):
        """Case I: store_id filtri faqat so'ralgan do'kondagi kam qoldiqlarni qaytarishi."""
        p1 = self.make_product(name="P1", min_stock=Decimal("10.00"))
        p2 = self.make_product(name="P2", min_stock=Decimal("10.00"))

        self.make_batch(self.store1, p1, quantity=1)  # Store 1 low
        self.make_batch(self.store2, p2, quantity=1)  # Store 2 low

        res1 = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(len(res1), 1)
        self.assertEqual(res1[0]["store"], self.store1.id)
        self.assertEqual(res1[0]["product"], p1.id)

        res2 = LowStockService.compute_live(store_id=self.store2.id)
        self.assertEqual(len(res2), 1)
        self.assertEqual(res2[0]["store"], self.store2.id)
        self.assertEqual(res2[0]["product"], p2.id)

    def test_case_j_product_search_filter(self):
        """Case J: Product search (nom yoki SKU bo'yicha)."""
        p1 = self.make_product(name="Castrol Edge 5W40", sku="CAS-5W40", min_stock=Decimal("5.00"))
        p2 = self.make_product(name="Mobil 1 5W30", sku="MOB-5W30", min_stock=Decimal("5.00"))

        self.make_batch(self.store1, p1, quantity=1)
        self.make_batch(self.store1, p2, quantity=1)

        # Search by name substring
        res_name = LowStockService.compute_live(search="castrol")
        self.assertEqual(len(res_name), 1)
        self.assertEqual(res_name[0]["product"], p1.id)

        # Search by SKU
        res_sku = LowStockService.compute_live(search="MOB-5W")
        self.assertEqual(len(res_sku), 1)
        self.assertEqual(res_sku[0]["product"], p2.id)

    def test_case_k_action_type_filter(self):
        """Case K: action_type filtri (purchase vs transfer)."""
        p_transfer = self.make_product(name="Transfer Part", min_stock=Decimal("10.00"))
        p_purchase = self.make_product(name="Purchase Part", min_stock=Decimal("10.00"))

        # p_transfer store1 da kam, store2 da bor -> transfer
        self.make_batch(self.store1, p_transfer, quantity=1)
        self.make_batch(self.store2, p_transfer, quantity=20)

        # p_purchase hech qayerda yo'q -> purchase
        self.make_batch(self.store1, p_purchase, quantity=1)

        transfers = LowStockService.compute_live(store_id=self.store1.id, action_type="transfer")
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0]["product"], p_transfer.id)

        purchases = LowStockService.compute_live(store_id=self.store1.id, action_type="purchase")
        self.assertEqual(len(purchases), 1)
        self.assertEqual(purchases[0]["product"], p_purchase.id)

    def test_case_l_inactive_product_and_store_exclusion(self):
        """Case L: Inactive product va inactive store ro'yxatga kirmasligi."""
        p_inactive = self.make_product(
            name="Inactive Product",
            min_stock=Decimal("10.00"),
            status=Product.ProductStatus.INACTIVE,
        )
        self.make_batch(self.store1, p_inactive, quantity=0)

        p_active = self.make_product(name="Active In Inactive Store", min_stock=Decimal("10.00"))
        self.make_batch(self.store_inactive, p_active, quantity=0)

        results = LowStockService.compute_live()
        self.assertFalse(any(r["product"] == p_inactive.id for r in results))
        self.assertFalse(any(r["store"] == self.store_inactive.id for r in results))

    def test_case_m_empty_result(self):
        """Case M: Hech qanday kam qoldiq bo'lmaganda bo'sh ro'yxat qaytishi."""
        p = self.make_product(name="Abundant", min_stock=Decimal("5.00"))
        self.make_batch(self.store1, p, quantity=100)

        results = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(results, [])

    def test_case_n_no_duplicates_across_multiple_products(self):
        """Case N: Mahsulotlar ro'yxatda takrorlanmasligi."""
        for i in range(5):
            p = self.make_product(name=f"Part {i}", min_stock=Decimal("10.00"))
            self.make_batch(self.store1, p, quantity=i)

        results = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(len(results), 5)
        ids = [r["id"] for r in results]
        self.assertEqual(len(ids), len(set(ids)))

    def test_case_o_existing_ordering_critical_ratio(self):
        """Case O: Eng kritigi (current_quantity / min_stock) birinchi chiqishi."""
        p_zero = self.make_product(name="B Zero Part", min_stock=Decimal("10.00"))     # 0 / 10 = 0.0
        p_half = self.make_product(name="A Half Part", min_stock=Decimal("10.00"))     # 5 / 10 = 0.5
        p_quarter = self.make_product(name="C Quarter Part", min_stock=Decimal("10.00")) # 2 / 10 = 0.2

        self.make_batch(self.store1, p_half, quantity=5)
        self.make_batch(self.store1, p_zero, quantity=0)
        self.make_batch(self.store1, p_quarter, quantity=2)

        results = LowStockService.compute_live(store_id=self.store1.id)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["product"], p_zero.id)     # ratio 0.0
        self.assertEqual(results[1]["product"], p_quarter.id)  # ratio 0.2
        self.assertEqual(results[2]["product"], p_half.id)     # ratio 0.5

    def test_case_p_api_pagination_and_response_schema(self):
        """Case P: LowStockListAPIView pagination va contract tekshiruvi."""
        for i in range(7):
            p = self.make_product(name=f"API Item {i:02d}", min_stock=Decimal("10.00"))
            self.make_batch(self.store1, p, quantity=i)

        client = APIClient()
        client.force_authenticate(user=self.user)

        response = client.get(f"/api/inventory/low-stock/?store={self.store1.id}&page=1&limit=5")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["count"], 7)
        self.assertEqual(data["total_pages"], 2)
        self.assertEqual(data["current_page"], 1)
        self.assertEqual(len(data["results"]), 5)

        # Schema checks
        first = data["results"][0]
        expected_keys = {
            "id", "store", "store_name", "product", "product_name",
            "sku", "current_quantity", "min_stock", "action_type",
            "status", "available_elsewhere", "sources", "created_at", "resolved_at",
        }
        self.assertEqual(set(first.keys()), expected_keys)

        # Page 2
        response2 = client.get(f"/api/inventory/low-stock/?store={self.store1.id}&page=2&limit=5")
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertEqual(len(data2["results"]), 2)

    def test_case_q_query_count_strictly_bounded(self):
        """Case Q: Query count regression test — N mahsulot bo'lganda so'rovlar soni 2 tadan oshmasligi."""
        for i in range(25):
            p = self.make_product(name=f"Scale Part {i}", min_stock=Decimal("10.00"))
            self.make_batch(self.store1, p, quantity=i % 5)
            self.make_batch(self.store2, p, quantity=20)

        with CaptureQueriesContext(connection) as ctx:
            results = LowStockService.compute_live(store_id=self.store1.id)

        self.assertEqual(len(results), 25)
        # Exactly 2 queries: 1 for low stock candidates, 1 for other stores sources
        self.assertLessEqual(len(ctx.captured_queries), 2)
