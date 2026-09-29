from decimal import Decimal
from django.test import RequestFactory, TestCase

from apps.contract.models import StockEntry, StockEntryItem, Supplier
from apps.contract.services.stock_entry_service import StockEntryService
from apps.products.models import Product, ProductBatch
from apps.products.serializers.product_crud_serializer import ProductListSerializer
from apps.products.services.product_query_service import annotate_latest_selling_price
from apps.store.models import Store
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.transfer.services.transfer_service import TransferService
from apps.users.models import User


class WholesalePricePreservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            phone_number="998909990001",
            full_name="Super Admin",
            password="secretpassword",
        )
        cls.store_warehouse = Store.objects.create(
            name="Avtoyon Ombor",
            phone_number="998901110001",
            address="Ombor address",
        )
        cls.store_branch = Store.objects.create(
            name="112 do'kon",
            phone_number="998901110002",
            address="112 address",
        )
        cls.supplier = Supplier.objects.create(
            name="Test Supplier Wholesale",
            phone_number="998901110003",
        )
        cls.factory = RequestFactory()

    def test_all_stores_stock_entry_333_overrides_older_batches_in_products_list(self):
        """
        StockEntry with 333/333/333 into Ombor must display 333/333/333 in Products List
        root even when 112-do'kon batch has an older wholesale price (222).
        """
        p = Product.objects.create(name="Universal Zajim 333", sku="ZJM-333")

        # 112 do'kon batch has older prices (e.g. 56 / 500 / 222)
        b_branch = ProductBatch.objects.create(
            product=p,
            store=self.store_branch,
            quantity=10,
            purchase_price=Decimal("56.00"),
            selling_price=Decimal("500.00"),
            wholesale_price=Decimal("222.00"),
        )

        # StockEntry into Ombor with 333 / 333 / 333
        entry = StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_warehouse,
            cash_amount=Decimal("3330.00"),
            card_amount=Decimal("0.00"),
            items=[{
                "product": p,
                "quantity": Decimal("10"),
                "purchase_price": Decimal("333.00"),
                "selling_price": Decimal("333.00"),
                "wholesale_price": Decimal("333.00"),
            }],
            user=self.superuser,
        )

        # Query all stores (store_id=None)
        qs = annotate_latest_selling_price(Product.objects.filter(id=p.id), store_id=None)
        item = qs.first()

        self.assertEqual(item.latest_purchase_price, Decimal("333.00"))
        self.assertEqual(item.latest_selling_price, Decimal("333.00"))
        self.assertEqual(item.latest_wholesale_price, Decimal("333.00"))

        request = self.factory.get("/api/v1/products/")
        all_stores = list(Store.objects.filter(is_active=True).order_by("name"))
        serializer_data = ProductListSerializer(item, context={"request": request, "all_stores": all_stores}).data

        self.assertEqual(Decimal(str(serializer_data["purchase_price"])), Decimal("333.00"))
        self.assertEqual(Decimal(str(serializer_data["selling_price"])), Decimal("333.00"))
        self.assertEqual(Decimal(str(serializer_data["wholesale_price"])), Decimal("333.00"))

    def test_zero_wholesale_preserves_existing_positive_wholesale_price(self):
        """
        Rule: If new StockEntry wholesale_price is 0 or empty:
        - eski = 222, yangi = 0 -> 222
        - eski = 222, yangi = 333 -> 333
        - eski = 0, yangi = 333 -> 333
        """
        # Scenario A: eski = 222, yangi = 0 -> 222
        p_a = Product.objects.create(name="Product Eski 222", sku="ESKI-222")
        b_a = ProductBatch.objects.create(
            product=p_a,
            store=self.store_warehouse,
            quantity=10,
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("200.00"),
            wholesale_price=Decimal("222.00"),
        )
        entry_a = StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_warehouse,
            cash_amount=Decimal("3000.00"),
            card_amount=Decimal("0.00"),
            items=[{
                "product": p_a,
                "quantity": Decimal("10"),
                "purchase_price": Decimal("300.00"),
                "selling_price": Decimal("350.00"),
                "wholesale_price": Decimal("0.00"),
            }],
            user=self.superuser,
        )
        sei_a = StockEntryItem.objects.filter(entry=entry_a, product=p_a).first()
        b_a.refresh_from_db()
        self.assertEqual(sei_a.wholesale_price, Decimal("222.00"))
        self.assertEqual(b_a.wholesale_price, Decimal("222.00"))

        qs_a = annotate_latest_selling_price(Product.objects.filter(id=p_a.id), store_id=None)
        item_a = qs_a.first()
        self.assertEqual(item_a.latest_wholesale_price, Decimal("222.00"))

        request = self.factory.get("/api/v1/products/")
        all_stores = list(Store.objects.filter(is_active=True).order_by("name"))
        data_a = ProductListSerializer(item_a, context={"request": request, "all_stores": all_stores}).data
        self.assertEqual(Decimal(str(data_a["wholesale_price"])), Decimal("222.00"))

        # Scenario B: eski = 222, yangi = 333 -> 333
        entry_b = StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_warehouse,
            cash_amount=Decimal("3300.00"),
            card_amount=Decimal("0.00"),
            items=[{
                "product": p_a,
                "quantity": Decimal("10"),
                "purchase_price": Decimal("300.00"),
                "selling_price": Decimal("350.00"),
                "wholesale_price": Decimal("333.00"),
            }],
            user=self.superuser,
        )
        sei_b = StockEntryItem.objects.filter(entry=entry_b, product=p_a).first()
        b_a.refresh_from_db()
        self.assertEqual(sei_b.wholesale_price, Decimal("333.00"))
        self.assertEqual(b_a.wholesale_price, Decimal("333.00"))

        qs_b = annotate_latest_selling_price(Product.objects.filter(id=p_a.id), store_id=None)
        item_b = qs_b.first()
        self.assertEqual(item_b.latest_wholesale_price, Decimal("333.00"))

        # Scenario C: eski = 0, yangi = 333 -> 333
        p_c = Product.objects.create(name="Product Eski 0", sku="ESKI-0")
        entry_c = StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_warehouse,
            cash_amount=Decimal("3300.00"),
            card_amount=Decimal("0.00"),
            items=[{
                "product": p_c,
                "quantity": Decimal("10"),
                "purchase_price": Decimal("200.00"),
                "selling_price": Decimal("300.00"),
                "wholesale_price": Decimal("333.00"),
            }],
            user=self.superuser,
        )
        sei_c = StockEntryItem.objects.filter(entry=entry_c, product=p_c).first()
        self.assertEqual(sei_c.wholesale_price, Decimal("333.00"))

        qs_c = annotate_latest_selling_price(Product.objects.filter(id=p_c.id), store_id=None)
        item_c = qs_c.first()
        self.assertEqual(item_c.latest_wholesale_price, Decimal("333.00"))

    def test_transfer_updates_destination_wholesale_without_affecting_cogs(self):
        """
        Ombor -> 112 transfer preserves transfer pricing fix:
        - Destination batch receives source wholesale price (333).
        - Source batch prices remain intact.
        - Lot COGS is tracked via StockLot.purchase_price.
        """
        p = Product.objects.create(name="Transfer Wholesale Test", sku="TR-WS-01")

        # Initial entry into Ombor: 333/333/333
        entry = StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_warehouse,
            cash_amount=Decimal("3330.00"),
            card_amount=Decimal("0.00"),
            items=[{
                "product": p,
                "quantity": Decimal("20"),
                "purchase_price": Decimal("333.00"),
                "selling_price": Decimal("333.00"),
                "wholesale_price": Decimal("333.00"),
            }],
            user=self.superuser,
        )

        # Initial batch in branch store had older 222
        branch_batch = ProductBatch.objects.create(
            product=p,
            store=self.store_branch,
            quantity=0,
            purchase_price=Decimal("56.00"),
            selling_price=Decimal("500.00"),
            wholesale_price=Decimal("222.00"),
        )

        # Transfer 5 units from Ombor to branch
        transfer = TransferService.create_transfer(
            from_store=self.store_warehouse,
            to_store=self.store_branch,
            items_data=[{
                "product": p,
                "quantity": Decimal("5"),
            }],
            user=self.superuser,
        )
        TransferService.approve_transfer(transfer_id=transfer.id, user=self.superuser)

        branch_batch.refresh_from_db()
        self.assertEqual(branch_batch.wholesale_price, Decimal("333.00"))
        self.assertEqual(branch_batch.purchase_price, Decimal("333.00"))
        self.assertEqual(branch_batch.selling_price, Decimal("333.00"))

        # Scoped query for branch store
        qs_branch = annotate_latest_selling_price(Product.objects.filter(id=p.id), store_id=self.store_branch.id)
        item_branch = qs_branch.first()
        self.assertEqual(item_branch.latest_wholesale_price, Decimal("333.00"))
        self.assertEqual(item_branch.latest_selling_price, Decimal("333.00"))
        self.assertEqual(item_branch.latest_purchase_price, Decimal("333.00"))
