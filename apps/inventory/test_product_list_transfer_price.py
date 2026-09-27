from decimal import Decimal
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.contract.models import StockEntry, StockEntryItem, Supplier
from apps.contract.services.stock_entry_service import StockEntryService
from apps.inventory.models import StockAllocation, StockLot
from apps.inventory.services.stock_allocation_service import StockAllocationService
from apps.products.models import Product, ProductBatch
from apps.products.serializers.product_crud_serializer import ProductListSerializer
from apps.products.utils.barcode_utility import normalize_barcode
from apps.products.views.product_crud_view import ProductListAPIView
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.sales.services.sale_return_service import SaleReturnService
from apps.sales.services.sales_services import SaleService
from apps.store.models import Store, StoreUser
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.transfer.services.transfer_service import TransferService
from apps.users.models import User


class ProductListTransferPriceCasesTests(TestCase):
    """
    Mandatory Business Verification Tests for Cases 1-7:
    CASE 1: B old = 500, A transfer = 999, A -> B.
            Products List = 999, Sales = 999.
    CASE 2: B has no stock, A -> B = 999.
            Products List = 999, Sales = 999.
    CASE 3: B has old stock, A -> B new price.
            Products List current price = new price, Old lot cost/qty unchanged.
    CASE 4: Multi-hop: A -> B -> C.
            C current price = transferred price (999).
    CASE 5: StockEntry new price.
            Products List and Sales both show new price.
    CASE 6: Store isolation: A/B/C stores have isolated prices.
    CASE 7: Sale + return: Price and refund consistency.
    """

    _barcode_seq = 8800

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            phone_number="998907777001",
            full_name="Super Admin",
            password="secretpassword",
        )
        cls.store_a = Store.objects.create(
            name="Store A (Warehouse)",
            phone_number="998901117777",
            address="Warehouse A",
            type=Store.StoreType.BASE,
        )
        cls.store_b = Store.objects.create(
            name="Store B (Retail 1)",
            phone_number="998902227777",
            address="Retail B",
            type=Store.StoreType.STORE,
        )
        cls.store_c = Store.objects.create(
            name="Store C (Retail 2)",
            phone_number="998903337777",
            address="Retail C",
            type=Store.StoreType.STORE,
        )

        StoreUser.objects.create(user=cls.superuser, store=cls.store_a, is_active=True)
        StoreUser.objects.create(user=cls.superuser, store=cls.store_b, is_active=True)
        StoreUser.objects.create(user=cls.superuser, store=cls.store_c, is_active=True)

        cls.supplier = Supplier.objects.create(
            name="Primary Supplier",
            phone_number="998904447777",
            address="Supplier Road",
        )
        cls.rf = RequestFactory()

    def create_product(self, name="Test Product"):
        ProductListTransferPriceCasesTests._barcode_seq += 1
        return Product.objects.create(
            name=name,
            barcode=normalize_barcode(f"{ProductListTransferPriceCasesTests._barcode_seq:012d}"),
        )

    def create_entry_lot(self, store, product, quantity, purchase_price, selling_price, wholesale_price=None):
        ws_price = wholesale_price or (selling_price * Decimal("0.9"))
        entry = StockEntry.objects.create(
            supplier=self.supplier,
            store=store,
            total_amount=quantity * purchase_price,
            cash_amount=quantity * purchase_price,
            created_by=self.superuser,
        )
        item = StockEntryItem.objects.create(
            entry=entry,
            product=product,
            quantity=quantity,
            purchase_price=purchase_price,
            selling_price=selling_price,
            wholesale_price=ws_price,
        )
        lot = StockLot.objects.create(
            store=store,
            product=product,
            supplier=self.supplier,
            stock_entry_item=item,
            lot_type=StockLot.LotType.PURCHASE,
            initial_quantity=quantity,
            remaining_quantity=quantity,
            purchase_price=purchase_price,
        )
        batch = StockAllocationService._sync_product_batch(store, product, sync_prices=True)
        batch.purchase_price = purchase_price
        batch.selling_price = selling_price
        batch.wholesale_price = ws_price
        batch.save(update_fields=["purchase_price", "selling_price", "wholesale_price"])
        return lot, item

    def transfer(self, from_store, to_store, product, quantity):
        transfer = StockTransfer.objects.create(
            from_store=from_store,
            to_store=to_store,
            status=StockTransfer.Status.APPROVED,
            created_by=self.superuser,
        )
        from_batch = ProductBatch.objects.filter(store=from_store, product=product).first()
        transfer_item = StockTransferItem.objects.create(
            stock_transfer=transfer,
            product=product,
            quantity=quantity,
            purchase_price=from_batch.purchase_price if from_batch else Decimal("0.00"),
            selling_price=from_batch.selling_price if from_batch else Decimal("0.00"),
        )
        out_allocs = StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)
        return transfer_item, out_allocs, in_allocs

    def get_product_list_data(self, product, store_id=None):
        url = "/api/products/"
        if store_id is not None:
            url += f"?store_id={store_id}"
        request = self.rf.get(url)
        request.user = self.superuser

        view = ProductListAPIView()
        view.request = request
        view.format_kwarg = None
        context = view.get_serializer_context()

        serializer = ProductListSerializer(product, context=context)
        return serializer.data

    def test_case_1_destination_has_old_price_updates_on_transfer(self):
        """
        CASE 1:
        B old = 500
        A transfer = 999
        A -> B
        Products List = 999
        Sales = 999
        """
        product = self.create_product("Product Case 1")

        # B has old stock @ 500
        self.create_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("20.00"),
            purchase_price=Decimal("400.00"),
            selling_price=Decimal("500.00"),
        )

        # A has stock @ 999
        self.create_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("700.00"),
            selling_price=Decimal("999.00"),
        )

        # Transfer 5 units A -> B
        self.transfer(self.store_a, self.store_b, product, Decimal("5.00"))

        # Check Sales price in Store B
        sales_price = StockAllocationService.resolve_selling_price(self.store_b, product)
        self.assertEqual(sales_price, Decimal("999.00"))

        # Check ProductBatch in Store B
        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.selling_price, Decimal("999.00"))

        # Check Products List API data for Store B
        list_data = self.get_product_list_data(product, store_id=self.store_b.id)
        self.assertEqual(Decimal(str(list_data["selling_price"])), Decimal("999.00"))

    def test_case_2_destination_has_no_prior_stock(self):
        """
        CASE 2:
        B has 0 stock
        A -> B = 999
        Products List = 999
        Sales = 999
        """
        product = self.create_product("Product Case 2")

        # A has stock @ 999
        self.create_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("700.00"),
            selling_price=Decimal("999.00"),
        )

        # Transfer 5 units A -> B
        self.transfer(self.store_a, self.store_b, product, Decimal("5.00"))

        # Check Sales price in Store B
        sales_price = StockAllocationService.resolve_selling_price(self.store_b, product)
        self.assertEqual(sales_price, Decimal("999.00"))

        # Check Products List API data for Store B
        list_data = self.get_product_list_data(product, store_id=self.store_b.id)
        self.assertEqual(Decimal(str(list_data["selling_price"])), Decimal("999.00"))

    def test_case_3_old_lot_cost_and_quantity_unchanged(self):
        """
        CASE 3:
        B old stock exists (cost=400, selling=500, qty=20).
        A -> B new price (cost=700, selling=999, qty=5).
        Products List current price = 999.
        Old lot cost and quantity unchanged in FIFO ledger.
        """
        product = self.create_product("Product Case 3")

        lot_b_old, _ = self.create_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("20.00"),
            purchase_price=Decimal("400.00"),
            selling_price=Decimal("500.00"),
        )

        self.create_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("700.00"),
            selling_price=Decimal("999.00"),
        )

        self.transfer(self.store_a, self.store_b, product, Decimal("5.00"))

        # Check Products List price is 999
        list_data = self.get_product_list_data(product, store_id=self.store_b.id)
        self.assertEqual(Decimal(str(list_data["selling_price"])), Decimal("999.00"))
        # Purchase price shown in Store B batches is active lot cost (400)
        batch_b_data = next(b for b in list_data["batches"] if b["store"] == self.store_b.id)
        self.assertEqual(Decimal(str(batch_b_data["purchase_price"])), Decimal("400.00"))

        # Verify old lot is untouched
        lot_b_old.refresh_from_db()
        self.assertEqual(lot_b_old.remaining_quantity, Decimal("20.00"))
        self.assertEqual(lot_b_old.purchase_price, Decimal("400.00"))

    def test_case_4_multi_hop_transfer_preserves_price(self):
        """
        CASE 4:
        A @ 999 -> B -> C
        C current price = transferred price (999).
        """
        product = self.create_product("Product Case 4")

        self.create_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("700.00"),
            selling_price=Decimal("999.00"),
        )

        # A -> B
        self.transfer(self.store_a, self.store_b, product, Decimal("8.00"))

        # B -> C
        self.transfer(self.store_b, self.store_c, product, Decimal("5.00"))

        # Check C price
        sales_price_c = StockAllocationService.resolve_selling_price(self.store_c, product)
        self.assertEqual(sales_price_c, Decimal("999.00"))

        list_data_c = self.get_product_list_data(product, store_id=self.store_c.id)
        self.assertEqual(Decimal(str(list_data_c["selling_price"])), Decimal("999.00"))

    def test_case_5_stock_entry_new_price(self):
        """
        CASE 5:
        StockEntry in Store B with new price.
        old = 500, new = 1100.
        Products List and Sales both show new price = 1100.
        """
        product = self.create_product("Product Case 5")

        self.create_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("400.00"),
            selling_price=Decimal("500.00"),
        )

        # New StockEntry via StockEntryService
        items_data = [
            {
                "product": product,
                "quantity": Decimal("15.00"),
                "purchase_price": Decimal("800.00"),
                "selling_price": Decimal("1100.00"),
                "wholesale_price": Decimal("1000.00"),
            }
        ]
        StockEntryService.create_entry(
            supplier=self.supplier,
            store=self.store_b,
            items=items_data,
            user=self.superuser,
            cash_amount=Decimal("12000.00"),
        )

        # Check Sales and Products List both = 1100
        sales_price = StockAllocationService.resolve_selling_price(self.store_b, product)
        self.assertEqual(sales_price, Decimal("1100.00"))

        list_data = self.get_product_list_data(product, store_id=self.store_b.id)
        self.assertEqual(Decimal(str(list_data["selling_price"])), Decimal("1100.00"))

    def test_case_6_store_isolation(self):
        """
        CASE 6:
        Store isolation:
        A = 999
        B = 500
        C = 700
        A -> B (5 units @ 999)
        Result:
        B = 999
        C = 700 (unchanged)
        A = 999 (unchanged)
        """
        product = self.create_product("Product Case 6")

        self.create_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("700.00"),
            selling_price=Decimal("999.00"),
        )
        self.create_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("20.00"),
            purchase_price=Decimal("400.00"),
            selling_price=Decimal("500.00"),
        )
        self.create_entry_lot(
            store=self.store_c,
            product=product,
            quantity=Decimal("15.00"),
            purchase_price=Decimal("550.00"),
            selling_price=Decimal("700.00"),
        )

        # Transfer A -> B
        self.transfer(self.store_a, self.store_b, product, Decimal("5.00"))

        # Verify Store B
        self.assertEqual(
            Decimal(str(self.get_product_list_data(product, store_id=self.store_b.id)["selling_price"])),
            Decimal("999.00"),
        )
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_b, product),
            Decimal("999.00"),
        )

        # Verify Store C is completely isolated
        self.assertEqual(
            Decimal(str(self.get_product_list_data(product, store_id=self.store_c.id)["selling_price"])),
            Decimal("700.00"),
        )
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_c, product),
            Decimal("700.00"),
        )

        # Verify Store A remains 999
        self.assertEqual(
            Decimal(str(self.get_product_list_data(product, store_id=self.store_a.id)["selling_price"])),
            Decimal("999.00"),
        )
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_a, product),
            Decimal("999.00"),
        )

    def test_case_7_sale_and_return_price_consistency(self):
        """
        CASE 7:
        B has old stock @ 500.
        A -> B @ 999.
        Sale made at current price 999.
        Return processed.
        Sale price and refund amount match (999).
        FIFO cost matches old lot (400).
        """
        product = self.create_product("Product Case 7")

        lot_b_old, _ = self.create_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("20.00"),
            purchase_price=Decimal("400.00"),
            selling_price=Decimal("500.00"),
        )
        self.create_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("700.00"),
            selling_price=Decimal("999.00"),
        )

        self.transfer(self.store_a, self.store_b, product, Decimal("5.00"))

        current_price_b = StockAllocationService.resolve_selling_price(self.store_b, product)
        self.assertEqual(current_price_b, Decimal("999.00"))

        # Sale 2 units at current price 999
        sale_data = {
            "store": self.store_b.id,
            "items": [
                {
                    "product": product.id,
                    "quantity": 2,
                    "price": current_price_b,
                }
            ],
            "payments": [{"type": "cash", "amount": Decimal("1998.00")}],
        }
        sale = SaleService.create_sale(user=self.superuser, data=sale_data)
        sale_item = sale.items.get(product=product)

        # Unit price is 999, FIFO purchase price is from old lot (400)
        self.assertEqual(sale_item.unit_price, Decimal("999.00"))
        self.assertEqual(sale_item.purchase_price, Decimal("400.00"))

        # Return 1 unit
        ret_data = {
            "sale": sale.id,
            "items": [
                {
                    "sale_item": sale_item.id,
                    "quantity": 1,
                }
            ],
            "payment_type": "cash",
        }
        ret_obj = SaleReturnService.create_return(user=self.superuser, data=ret_data)
        self.assertEqual(ret_obj.total_refund, Decimal("999.00"))

        # Check Products List still shows 999
        list_data = self.get_product_list_data(product, store_id=self.store_b.id)
        self.assertEqual(Decimal(str(list_data["selling_price"])), Decimal("999.00"))
