from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.contract.models import StockEntry, StockEntryItem, Supplier
from apps.contract.services.stock_entry_service import StockEntryService
from apps.inventory.models import StockAllocation, StockLot
from apps.inventory.services.stock_allocation_service import StockAllocationService
from apps.products.models import Product, ProductBatch
from apps.products.utils.barcode_utility import normalize_barcode
from apps.sales.models import Sale, SaleItem
from apps.sales.services.sales_services import SaleService
from apps.store.models import Store, StoreUser
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.transfer.services.transfer_service import TransferService
from apps.users.models import User


class TransferDisplayPricingAuditTests(TestCase):
    """
    Acceptance Tests to Audit & Prove Transfer Pricing Bug (RED test):
    CASE 1: Store A has ProductBatch=777/999/900, StockLot cost=56.
            A -> B transfer.
            Expected B ProductBatch = 777/999/900.
            Expected B StockLot cost = 56.
            Expected Transfer Item purchase_price = 777.

    CASE 2: Store B had old ProductBatch = 100/200/150.
            A transfers 777/999/900 to B.
            Expected B ProductBatch = 777/999/900.
            Expected B old lot cost remains 100.

    CASE 3: Store A has old lot cost = 56, ProductBatch purchase = 777.
            Sale in Store A.
            SaleItem.purchase_price must be 56 (lot acquisition cost, NOT 777).

    CASE 4: A -> B -> C multi-hop transfer.
            Store C ProductBatch = 777/999/900.
            Store C StockLot cost = 56.
            Sale in C must have COGS = 56.

    CASE 5: 0-price preservation rule on transfer.
    """

    _barcode_seq = 9500

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            phone_number="998909998801",
            full_name="Super Admin",
            password="secretpassword",
        )
        cls.store_a = Store.objects.create(
            name="Store A (Warehouse)",
            phone_number="998901118801",
            address="Warehouse A",
            type=Store.StoreType.BASE,
        )
        cls.store_b = Store.objects.create(
            name="Store B (112-do'kon)",
            phone_number="998902228801",
            address="Store 112",
            type=Store.StoreType.STORE,
        )
        cls.store_c = Store.objects.create(
            name="Store C (Retail 2)",
            phone_number="998903338801",
            address="Retail C",
            type=Store.StoreType.STORE,
        )
        StoreUser.objects.create(user=cls.superuser, store=cls.store_a, is_active=True)
        StoreUser.objects.create(user=cls.superuser, store=cls.store_b, is_active=True)
        StoreUser.objects.create(user=cls.superuser, store=cls.store_c, is_active=True)

        cls.supplier = Supplier.objects.create(
            name="Primary Supplier",
            phone_number="998909990001",
        )

    def create_product(self, name: str) -> Product:
        TransferDisplayPricingAuditTests._barcode_seq += 1
        return Product.objects.create(
            name=name,
            barcode=normalize_barcode(f"{TransferDisplayPricingAuditTests._barcode_seq:012d}"),
        )

    def test_case_1_transfer_price_display_vs_cogs(self):
        """
        CASE 1:
        Store A:
        Old lot cost = 56 (10 units).
        New entry: purchase=777, selling=999, wholesale=900 (50 units).
        So Store A ProductBatch = 777 / 999 / 900.
        A -> B transfer (5 units).

        EXPECTED:
        - Transfer Item purchase_price = 777.00 (source display purchase price).
        - Store B ProductBatch: 777 / 999 / 900.
        - Store B StockLot cost: 56.00 (FIFO acquisition cost).
        """
        product = self.create_product("Audit Case 1 Product")

        # Step 1: Old StockEntry in Store A @ 56.00
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_a,
            items=[{
                "product": product,
                "quantity": Decimal("10"),
                "purchase_price": Decimal("56.00"),
                "selling_price": Decimal("500.00"),
                "wholesale_price": Decimal("0.00"),
            }],
            user=self.superuser,
        )

        # Step 2: New StockEntry in Store A @ 777 / 999 / 900
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_a,
            items=[{
                "product": product,
                "quantity": Decimal("50"),
                "purchase_price": Decimal("777.00"),
                "selling_price": Decimal("999.00"),
                "wholesale_price": Decimal("900.00"),
            }],
            user=self.superuser,
        )

        batch_a = ProductBatch.objects.get(store=self.store_a, product=product)
        self.assertEqual(batch_a.purchase_price, Decimal("777.00"))
        self.assertEqual(batch_a.selling_price, Decimal("999.00"))
        self.assertEqual(batch_a.wholesale_price, Decimal("900.00"))

        # Step 3: Transfer 5 units A -> B
        transfer = TransferService.create_transfer(
            from_store=self.store_a,
            to_store=self.store_b,
            items_data=[{"product": product, "quantity": Decimal("5")}],
            user=self.superuser,
        )
        TransferService.approve_transfer(transfer_id=transfer.id, user=self.superuser)

        transfer_item = transfer.items.get(product=product)
        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        dest_lots = StockLot.objects.filter(store=self.store_b, product=product)

        # 1. Transfer document display price must be 777.00 (NOT 56.00)
        self.assertEqual(
            transfer_item.purchase_price, Decimal("777.00"),
            f"TransferItem.purchase_price was overwritten with {transfer_item.purchase_price}, expected 777.00!"
        )

        # 2. Destination ProductBatch must be 777 / 999 / 900 (NOT 56 / 999 / 900)
        self.assertEqual(
            batch_b.purchase_price, Decimal("777.00"),
            f"Store B ProductBatch.purchase_price is {batch_b.purchase_price}, expected 777.00!"
        )
        self.assertEqual(batch_b.selling_price, Decimal("999.00"))
        self.assertEqual(batch_b.wholesale_price, Decimal("900.00"))

        # 3. Destination StockLot cost must remain 56.00 (the accounting FIFO cost)
        self.assertEqual(dest_lots.count(), 1)
        self.assertEqual(
            dest_lots.first().purchase_price, Decimal("56.00"),
            "StockLot.purchase_price must be 56.00 for FIFO/COGS accounting!"
        )

    def test_case_2_destination_prior_batch_overwritten_by_transfer(self):
        """
        CASE 2:
        Store B already has old batch 100 / 200 / 150 with old lot cost 100.
        Store A transfers 777 / 999 / 900 to B (while A's oldest lot cost is 56).

        EXPECTED:
        - Store B ProductBatch transitions to 777 / 999 / 900.
        - Store B old lot cost remains 100.
        - Store B newly received lot cost is 56.
        """
        product = self.create_product("Audit Case 2 Product")

        # Store B old stock: 10 @ 100 / 200 / 150
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_b,
            items=[{
                "product": product,
                "quantity": Decimal("10"),
                "purchase_price": Decimal("100.00"),
                "selling_price": Decimal("200.00"),
                "wholesale_price": Decimal("150.00"),
            }],
            user=self.superuser,
        )

        # Store A: 10 @ 56, then new entry @ 777 / 999 / 900
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_a,
            items=[{
                "product": product,
                "quantity": Decimal("10"),
                "purchase_price": Decimal("56.00"),
                "selling_price": Decimal("500.00"),
                "wholesale_price": Decimal("0.00"),
            }],
            user=self.superuser,
        )
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_a,
            items=[{
                "product": product,
                "quantity": Decimal("50"),
                "purchase_price": Decimal("777.00"),
                "selling_price": Decimal("999.00"),
                "wholesale_price": Decimal("900.00"),
            }],
            user=self.superuser,
        )

        # Transfer A -> B (5 units)
        transfer = TransferService.create_transfer(
            from_store=self.store_a,
            to_store=self.store_b,
            items_data=[{"product": product, "quantity": Decimal("5")}],
            user=self.superuser,
        )
        TransferService.approve_transfer(transfer_id=transfer.id, user=self.superuser)

        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(
            batch_b.purchase_price, Decimal("777.00"),
            f"Store B ProductBatch.purchase_price is {batch_b.purchase_price}, expected 777.00!"
        )
        self.assertEqual(batch_b.selling_price, Decimal("999.00"))
        self.assertEqual(batch_b.wholesale_price, Decimal("900.00"))

        # Verify old lot in B cost untouched
        old_lot_b = StockLot.objects.filter(store=self.store_b, product=product, lot_type=StockLot.LotType.PURCHASE).first()
        self.assertEqual(old_lot_b.purchase_price, Decimal("100.00"))

    def test_case_3_sale_cogs_uses_lot_cost_not_display_price(self):
        """
        CASE 3:
        Store A has old lot cost = 56, and ProductBatch purchase_price = 777.
        Sale is executed in Store A.

        EXPECTED:
        - SaleItem.purchase_price = 56.00 (COGS from FIFO lot).
        - ProductBatch.purchase_price = 777.00 (display price remains untouched by sale).
        """
        product = self.create_product("Audit Case 3 Product")

        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_a,
            items=[{
                "product": product,
                "quantity": Decimal("10"),
                "purchase_price": Decimal("56.00"),
                "selling_price": Decimal("500.00"),
                "wholesale_price": Decimal("0.00"),
            }],
            user=self.superuser,
        )
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_a,
            items=[{
                "product": product,
                "quantity": Decimal("50"),
                "purchase_price": Decimal("777.00"),
                "selling_price": Decimal("999.00"),
                "wholesale_price": Decimal("900.00"),
            }],
            user=self.superuser,
        )

        # Sale in Store A (2 units)
        sale = SaleService.create_sale(
            user=self.superuser,
            data={
                "store": self.store_a.id,
                "items": [{"product": product.id, "quantity": 2, "price": Decimal("999.00")}],
                "payments": [{"type": "cash", "amount": Decimal("1998.00")}],
            },
        )
        sale_item = sale.items.get(product=product)

        # COGS must strictly be historical lot cost 56.00
        self.assertEqual(sale_item.purchase_price, Decimal("56.00"))

        # Display price on batch must remain 777.00
        batch_a = ProductBatch.objects.get(store=self.store_a, product=product)
        self.assertEqual(batch_a.purchase_price, Decimal("777.00"))

    def test_case_4_multi_hop_transfer_lineage(self):
        """
        CASE 4:
        A -> B -> C multi-hop.
        Store A has old lot cost = 56, ProductBatch = 777 / 999 / 900.

        EXPECTED:
        - Store B ProductBatch = 777 / 999 / 900, lot cost = 56.
        - Store C ProductBatch = 777 / 999 / 900, lot cost = 56.
        - Sale in Store C has COGS = 56.00.
        """
        product = self.create_product("Audit Case 4 Product")

        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_a,
            items=[{
                "product": product,
                "quantity": Decimal("10"),
                "purchase_price": Decimal("56.00"),
                "selling_price": Decimal("500.00"),
                "wholesale_price": Decimal("0.00"),
            }],
            user=self.superuser,
        )
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_a,
            items=[{
                "product": product,
                "quantity": Decimal("50"),
                "purchase_price": Decimal("777.00"),
                "selling_price": Decimal("999.00"),
                "wholesale_price": Decimal("900.00"),
            }],
            user=self.superuser,
        )

        # Hop 1: A -> B (5 units)
        t_ab = TransferService.create_transfer(
            from_store=self.store_a,
            to_store=self.store_b,
            items_data=[{"product": product, "quantity": Decimal("5")}],
            user=self.superuser,
        )
        TransferService.approve_transfer(transfer_id=t_ab.id, user=self.superuser)

        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(
            batch_b.purchase_price, Decimal("777.00"),
            f"Store B ProductBatch.purchase_price is {batch_b.purchase_price}, expected 777.00!"
        )

        # Hop 2: B -> C (3 units)
        t_bc = TransferService.create_transfer(
            from_store=self.store_b,
            to_store=self.store_c,
            items_data=[{"product": product, "quantity": Decimal("3")}],
            user=self.superuser,
        )
        TransferService.approve_transfer(transfer_id=t_bc.id, user=self.superuser)

        batch_c = ProductBatch.objects.get(store=self.store_c, product=product)
        self.assertEqual(
            batch_c.purchase_price, Decimal("777.00"),
            f"Store C ProductBatch.purchase_price is {batch_c.purchase_price}, expected 777.00!"
        )
        self.assertEqual(batch_c.selling_price, Decimal("999.00"))
        self.assertEqual(batch_c.wholesale_price, Decimal("900.00"))

        lot_c = StockLot.objects.filter(store=self.store_c, product=product).first()
        self.assertEqual(lot_c.purchase_price, Decimal("56.00"))

        # Sale in Store C
        sale_c = SaleService.create_sale(
            user=self.superuser,
            data={
                "store": self.store_c.id,
                "items": [{"product": product.id, "quantity": 1, "price": Decimal("999.00")}],
                "payments": [{"type": "cash", "amount": Decimal("999.00")}],
            },
        )
        self.assertEqual(sale_c.items.get(product=product).purchase_price, Decimal("56.00"))

    def test_case_5_zero_price_rule_on_transfer(self):
        """
        CASE 5:
        Store B already has ProductBatch = 100 / 200 / 150.
        Transfer happens where one component is 0 (or not updated).
        Existing positive price on B must not be clobbered to 0.
        """
        product = self.create_product("Audit Case 5 Product")

        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_b,
            items=[{
                "product": product,
                "quantity": Decimal("10"),
                "purchase_price": Decimal("100.00"),
                "selling_price": Decimal("200.00"),
                "wholesale_price": Decimal("150.00"),
            }],
            user=self.superuser,
        )

        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_a,
            items=[{
                "product": product,
                "quantity": Decimal("20"),
                "purchase_price": Decimal("300.00"),
                "selling_price": Decimal("500.00"),
                "wholesale_price": Decimal("0.00"), # 0 wholesale
            }],
            user=self.superuser,
        )

        transfer = TransferService.create_transfer(
            from_store=self.store_a,
            to_store=self.store_b,
            items_data=[{"product": product, "quantity": Decimal("5")}],
            user=self.superuser,
        )
        TransferService.approve_transfer(transfer_id=transfer.id, user=self.superuser)

        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        # Purchase & selling should be from transfer (300 / 500)
        self.assertEqual(batch_b.purchase_price, Decimal("300.00"))
        self.assertEqual(batch_b.selling_price, Decimal("500.00"))
        # Wholesale was 0 in source entry, so B's existing 150 must be preserved
        self.assertEqual(batch_b.wholesale_price, Decimal("150.00"))
