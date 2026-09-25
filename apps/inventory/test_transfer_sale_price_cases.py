from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.contract.models import StockEntry, StockEntryItem, Supplier
from apps.contract.services.stock_entry_service import StockEntryService
from apps.inventory.models import StockAllocation, StockLot
from apps.inventory.services.stock_allocation_service import StockAllocationService
from apps.products.models import Product, ProductBatch
from apps.products.utils.barcode_utility import normalize_barcode
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.sales.services.sale_return_service import SaleReturnService
from apps.sales.services.sales_services import SaleService
from apps.store.models import Store, StoreUser
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.transfer.services.transfer_service import TransferService
from apps.users.models import User


class TransferSalePriceCasesTests(TestCase):
    """
    Mandatory Business Verification Tests for Cases 1-6:
    CASE 1: B = 20 @ 120k, A = 5 @ 150k. Transfer A -> B 5 units.
            Next sale in B must be 150k.
    CASE 2: B has 0 stock. A -> B 5 @ 150k.
            Sale in B must be 150k.
    CASE 3: B has old stock + new transfer arrives.
            Historical cost/lot data not altered, current selling price transitions to 150k.
    CASE 4: A -> B -> C multi-hop transfer.
            Price lineage preserved.
    CASE 5: Transfer -> sale -> return.
            Sale price and return refund amount match (150k).
    CASE 6: Normal StockEntry in B maintains existing working price behavior.
    """

    _barcode_seq = 9700

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            phone_number="998908888001",
            full_name="Super Admin",
            password="secretpassword",
        )
        cls.store_a = Store.objects.create(
            name="Store A (Warehouse)",
            phone_number="998901118888",
            address="Warehouse A",
            type=Store.StoreType.BASE,
        )
        cls.store_b = Store.objects.create(
            name="Store B (Retail 1)",
            phone_number="998902228888",
            address="Retail B",
            type=Store.StoreType.STORE,
        )
        cls.store_c = Store.objects.create(
            name="Store C (Retail 2)",
            phone_number="998903338888",
            address="Retail C",
            type=Store.StoreType.STORE,
        )

        StoreUser.objects.create(user=cls.superuser, store=cls.store_a, is_active=True)
        StoreUser.objects.create(user=cls.superuser, store=cls.store_b, is_active=True)
        StoreUser.objects.create(user=cls.superuser, store=cls.store_c, is_active=True)

        cls.supplier = Supplier.objects.create(
            name="Primary Supplier",
            phone_number="998904448888",
            address="Supplier Road",
        )

    def create_product(self, name="Test Product"):
        TransferSalePriceCasesTests._barcode_seq += 1
        return Product.objects.create(
            name=name,
            barcode=normalize_barcode(f"{TransferSalePriceCasesTests._barcode_seq:012d}"),
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
        # In actual transfer flow, transfer item takes from_store's batch selling price
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

    def test_case_1_destination_has_old_stock_sells_at_new_price(self):
        """
        CASE 1:
        B = 20 dona @ 120k (cost 100k)
        A = 5 dona @ 150k (cost 125k)
        A -> B 5 dona
        B'da keyingi yangi sotuv narxi 150k bo'lishi kerak.
        """
        product = self.create_product("Product Case 1")

        # B store has 20 units @ 120k
        lot_b, _ = self.create_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("20.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("120000.00"),
        )

        # A store has 5 units @ 150k
        lot_a, _ = self.create_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("125000.00"),
            selling_price=Decimal("150000.00"),
        )

        # Transfer 5 units from A to B
        transfer_item, out_allocs, in_allocs = self.transfer(
            self.store_a, self.store_b, product, Decimal("5.00")
        )

        # Check B's ProductBatch current selling price
        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.quantity, Decimal("25.00"))
        self.assertEqual(batch_b.selling_price, Decimal("150000.00"))

        # Check selling price resolution
        current_price_b = StockAllocationService.resolve_selling_price(self.store_b, product)
        self.assertEqual(current_price_b, Decimal("150000.00"))

        # Make a sale in B: Cashier sells at the current store price (150 000)
        sale_data = {
            "store": self.store_b.id,
            "items": [
                {
                    "product": product.id,
                    "quantity": 1,
                    "price": current_price_b,
                }
            ],
            "payments": [{"type": "cash", "amount": Decimal("150000.00")}],
        }
        sale = SaleService.create_sale(user=self.superuser, data=sale_data)
        sale_item = sale.items.get(product=product)

        # Customer paid 150 000
        self.assertEqual(sale_item.unit_price, Decimal("150000.00"))
        # FIFO cost is from old lot (100 000)
        self.assertEqual(sale_item.purchase_price, Decimal("100000.00"))

        # Check lot remaining
        lot_b.refresh_from_db()
        self.assertEqual(lot_b.remaining_quantity, Decimal("19.00"))

    def test_case_2_destination_has_no_stock_sells_at_150k(self):
        """
        CASE 2:
        B'da stock yo'q
        A -> B 5 dona @ 150k
        B'dagi sotuv 150k bo'lishi kerak.
        """
        product = self.create_product("Product Case 2")

        # A store has 5 units @ 150k
        self.create_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("125000.00"),
            selling_price=Decimal("150000.00"),
        )

        # B store has 0 stock
        batch_b = ProductBatch.objects.filter(store=self.store_b, product=product).first()
        self.assertTrue(batch_b is None or batch_b.quantity == Decimal("0.00"))

        # Transfer A -> B
        self.transfer(self.store_a, self.store_b, product, Decimal("5.00"))

        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.quantity, Decimal("5.00"))
        self.assertEqual(batch_b.selling_price, Decimal("150000.00"))
        self.assertEqual(batch_b.purchase_price, Decimal("125000.00"))

        resolved_price = StockAllocationService.resolve_selling_price(self.store_b, product)
        self.assertEqual(resolved_price, Decimal("150000.00"))

        # Sell in B
        sale_data = {
            "store": self.store_b.id,
            "items": [
                {
                    "product": product.id,
                    "quantity": 2,
                    "price": resolved_price,
                }
            ],
            "payments": [{"type": "cash", "amount": Decimal("300000.00")}],
        }
        sale = SaleService.create_sale(user=self.superuser, data=sale_data)
        sale_item = sale.items.get(product=product)

        self.assertEqual(sale_item.unit_price, Decimal("150000.00"))
        self.assertEqual(sale_item.purchase_price, Decimal("125000.00"))

    def test_case_3_historical_cost_preserved_selling_price_transitions(self):
        """
        CASE 3:
        B'da eski stock bor + yangi transfer keladi.
        Eski stockning historical cost/lot ma'lumotlari buzilmasin,
        lekin current selling price transferdagi yangi narxga o'tsin.
        """
        product = self.create_product("Product Case 3")

        # B store has old lot: 20 units @ cost 100k, selling 120k
        old_lot_b, _ = self.create_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("20.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("120000.00"),
        )

        # A store has new lot: 10 units @ cost 130k, selling 160k
        new_lot_a, _ = self.create_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("130000.00"),
            selling_price=Decimal("160000.00"),
        )

        # Transfer 5 units from A to B
        transfer_item, out_allocs, in_allocs = self.transfer(
            self.store_a, self.store_b, product, Decimal("5.00")
        )

        # Check: Old lot's historical cost and data in B are completely preserved!
        old_lot_b.refresh_from_db()
        self.assertEqual(old_lot_b.purchase_price, Decimal("100000.00"))
        self.assertEqual(old_lot_b.remaining_quantity, Decimal("20.00"))

        # Check: New transferred lot in B has its own cost and selling price
        dest_lot = in_allocs[0].lot
        self.assertEqual(dest_lot.purchase_price, Decimal("130000.00"))
        self.assertEqual(dest_lot.selling_price, Decimal("160000.00"))
        self.assertEqual(dest_lot.remaining_quantity, Decimal("5.00"))
        self.assertEqual(dest_lot.source_lot, new_lot_a)

        # Check: B's current selling price has transitioned to 160 000!
        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.selling_price, Decimal("160000.00"))
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_b, product),
            Decimal("160000.00"),
        )

    def test_case_4_multi_hop_transfer_preserves_lineage(self):
        """
        CASE 4:
        A -> B -> C multi-hop transfer.
        Narx lineage yo'qolmasin.
        """
        product = self.create_product("Product Case 4")

        # Initial entry at Store A @ 150k
        lot_a, _ = self.create_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("110000.00"),
            selling_price=Decimal("150000.00"),
        )

        # Hop 1: A -> B (6 units)
        _, _, in_b = self.transfer(self.store_a, self.store_b, product, Decimal("6.00"))
        lot_b = in_b[0].lot

        self.assertEqual(lot_b.source_lot, lot_a)
        self.assertEqual(lot_b.purchase_price, Decimal("110000.00"))
        self.assertEqual(lot_b.selling_price, Decimal("150000.00"))
        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.selling_price, Decimal("150000.00"))

        # Hop 2: B -> C (4 units)
        _, _, in_c = self.transfer(self.store_b, self.store_c, product, Decimal("4.00"))
        lot_c = in_c[0].lot

        self.assertEqual(lot_c.source_lot, lot_b)
        self.assertEqual(lot_c.source_lot.source_lot, lot_a)
        self.assertEqual(lot_c.purchase_price, Decimal("110000.00"))
        self.assertEqual(lot_c.selling_price, Decimal("150000.00"))
        self.assertEqual(lot_c.supplier, self.supplier)

        batch_c = ProductBatch.objects.get(store=self.store_c, product=product)
        self.assertEqual(batch_c.selling_price, Decimal("150000.00"))
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_c, product),
            Decimal("150000.00"),
        )

    def test_case_5_sale_and_return_matching_price(self):
        """
        CASE 5:
        Transferdan keyin sale + return.
        Sale price va return amount bir-biriga mos bo'lsin.
        """
        product = self.create_product("Product Case 5")

        # B has 10 @ 120k
        old_lot, _ = self.create_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("120000.00"),
        )

        # A has 5 @ 150k
        self.create_entry_lot(
            store=self.store_a,
            product=product,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("125000.00"),
            selling_price=Decimal("150000.00"),
        )

        # Transfer A -> B
        self.transfer(self.store_a, self.store_b, product, Decimal("5.00"))

        current_price = StockAllocationService.resolve_selling_price(self.store_b, product)
        self.assertEqual(current_price, Decimal("150000.00"))

        # Sale 2 units in Store B @ 150 000
        sale_data = {
            "store": self.store_b.id,
            "items": [
                {
                    "product": product.id,
                    "quantity": 2,
                    "price": current_price,
                }
            ],
            "payments": [{"type": "cash", "amount": Decimal("300000.00")}],
        }
        sale = SaleService.create_sale(user=self.superuser, data=sale_data)
        sale_item = sale.items.get(product=product)

        self.assertEqual(sale_item.unit_price, Decimal("150000.00"))
        self.assertEqual(sale_item.total_price, Decimal("300000.00"))

        # Return 1 unit via SaleReturnService
        return_data = {
            "sale": sale.id,
            "items": [
                {
                    "sale_item": sale_item.id,
                    "quantity": 1,
                }
            ],
        }
        ret = SaleReturnService.create_return(user=self.superuser, data=return_data)
        ret_item = ret.items.get(sale_item=sale_item)

        # Crucial check: Return refund amount must be exactly 150 000 (matching sale price!)
        self.assertEqual(ret_item.unit_price, Decimal("150000.00"))
        self.assertEqual(ret_item.total_price, Decimal("150000.00"))
        self.assertEqual(ret.total_refund, Decimal("150000.00"))

    def test_case_6_direct_stock_entry_behavior_preserved(self):
        """
        CASE 6:
        Oddiy StockEntry orqali B'ga yangi mahsulot kirim qilinganda
        mavjud ishlayotgan price behavior o'zgarmasin.
        """
        product = self.create_product("Product Case 6")

        # Initial entry in Store B: 10 units @ 100k / 120k
        lot_1, _ = self.create_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("120000.00"),
        )
        batch = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch.quantity, Decimal("10.00"))
        self.assertEqual(batch.selling_price, Decimal("120000.00"))

        # Direct StockEntry into Store B with new price: 5 units @ 140k / 170k
        lot_2, _ = self.create_entry_lot(
            store=self.store_b,
            product=product,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("140000.00"),
            selling_price=Decimal("170000.00"),
        )

        batch.refresh_from_db()
        self.assertEqual(batch.quantity, Decimal("15.00"))
        self.assertEqual(batch.selling_price, Decimal("170000.00"))
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_b, product),
            Decimal("170000.00"),
        )

        # Sell 1 unit
        sale_data = {
            "store": self.store_b.id,
            "items": [
                {
                    "product": product.id,
                    "quantity": 1,
                    "price": Decimal("170000.00"),
                }
            ],
            "payments": [{"type": "cash", "amount": Decimal("170000.00")}],
        }
        sale = SaleService.create_sale(user=self.superuser, data=sale_data)
        sale_item = sale.items.get(product=product)

        self.assertEqual(sale_item.unit_price, Decimal("170000.00"))
        self.assertEqual(sale_item.purchase_price, Decimal("100000.00"))  # FIFO cost from lot 1
