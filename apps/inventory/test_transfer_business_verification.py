from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.contract.models import StockEntry, StockEntryItem, Supplier
from apps.inventory.models import StockAllocation, StockLot
from apps.inventory.services.stock_allocation_service import StockAllocationService
from apps.products.models import Product, ProductBatch
from apps.products.utils.barcode_utility import normalize_barcode
from apps.sales.models import Sale, SaleItem
from apps.sales.services.sales_services import SaleService
from apps.store.models import Store, StoreUser
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.users.models import User


class TransferBusinessVerificationTests(TestCase):
    """
    Dedicated Business Verification Test for:
    - CASE A: Destination has old stock (20 @ 100k/120k).
              Transfer 5 @ 150k/180k from Store A.
              Verify remaining quantities, prices, and sale behavior.
    - CASE B: Destination has NO old stock (0 units).
              Transfer 5 @ 150k/180k from Store A.
              Verify remaining quantities, prices, and sale behavior.
    """

    _barcode_seq = 9500

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            phone_number="998909999001",
            full_name="Super Admin",
            password="secretpassword",
        )
        cls.store_a = Store.objects.create(
            name="Store A (Central)",
            phone_number="998901119999",
            address="Central Warehouse",
            type=Store.StoreType.BASE,
        )
        cls.store_b = Store.objects.create(
            name="Store B (Branch)",
            phone_number="998902229999",
            address="Branch Store",
            type=Store.StoreType.STORE,
        )
        StoreUser.objects.create(user=cls.superuser, store=cls.store_a, is_active=True)
        StoreUser.objects.create(user=cls.superuser, store=cls.store_b, is_active=True)

        cls.supplier = Supplier.objects.create(
            name="Auto Parts Supplier",
            phone_number="998903339999",
            address="Industrial Zone",
        )

    def create_product(self, name="Product X"):
        TransferBusinessVerificationTests._barcode_seq += 1
        return Product.objects.create(
            name=name,
            barcode=normalize_barcode(f"{TransferBusinessVerificationTests._barcode_seq:012d}"),
        )

    def create_entry_lot(self, store, product, quantity, purchase_price, selling_price):
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
            wholesale_price=selling_price * Decimal("0.9"),
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
        StockAllocationService._sync_product_batch(store, product, sync_prices=True)
        return lot, item

    def transfer_lot_quantity(self, from_store, to_store, product, quantity):
        transfer = StockTransfer.objects.create(
            from_store=from_store,
            to_store=to_store,
            status=StockTransfer.Status.APPROVED,
            created_by=self.superuser,
        )
        transfer_item = StockTransferItem.objects.create(
            stock_transfer=transfer,
            product=product,
            quantity=quantity,
            purchase_price=Decimal("0.00"),
            selling_price=Decimal("0.00"),
        )
        out_allocs = StockAllocationService.allocate_transfer_out(transfer_item=transfer_item)
        in_allocs = StockAllocationService.allocate_transfer_in(transfer_item=transfer_item)
        return transfer_item, out_allocs, in_allocs

    def test_case_a_destination_has_old_stock(self):
        """
        CASE A:
        Store A:
          - Lot A1 (old): 10 @ 100k / 120k
          - Lot A2 (new): 10 @ 150k / 180k
        Store B:
          - Lot B_old: 20 @ 100k / 120k

        A'dan B'ga yangi 150k / 180k lotdan 5 dona transfer:
        1. Store A'da Lot A1 (10 dona) sotilib tugatiladi, shunda faol lot Lot A2 bo'ladi.
        2. Store A'dan Store B'ga 5 dona transfer qilinadi.
        3. Store B'dagi holat tekshiriladi:
           - Lot B_old: 20 dona @ 100k / 120k (o'zgarmasdan saqlanadi)
           - Lot B_transferred: 5 dona @ 150k / 180k (yangi narx va lineage)
           - ProductBatch B: jami 25 dona, purchase_price = 100k, selling_price = 120k (eski narx overwrite bo'lmagan)
        4. Store B'da sotuv amalga oshiriladi:
           - 1-sotuv (5 dona sotilganda):
             FIFO bo'yicha Lot B_old'dan (eski narx 120k, tannarx 100k) sotiladi.
             Lot B_old 15 dona qoladi, Lot B_transferred esa 5 dona bo'lib turadi.
           - 2-sotuv (eski 20 dona to'liq tugagach, masalan qolgan 15 dona + yangidan 2 dona = 17 dona sotilganda):
             Lot B_old 0 bo'ladi, yangi lotdan 2 dona sotiladi (tannarx 150k).
             Batch selling_price avtomatik 180k ga, purchase_price 150k ga o'tadi!
        """
        product = self.create_product("Product X - Case A")

        # 1. Setup Store A lots
        lot_a1, _ = self.create_entry_lot(self.store_a, product, Decimal("10.00"), Decimal("100000.00"), Decimal("120000.00"))
        lot_a2, _ = self.create_entry_lot(self.store_a, product, Decimal("10.00"), Decimal("150000.00"), Decimal("180000.00"))

        # 2. Setup Store B old stock
        lot_b_old, _ = self.create_entry_lot(self.store_b, product, Decimal("20.00"), Decimal("100000.00"), Decimal("120000.00"))

        # 3. Store A exhausts Lot A1 so that transfer takes specifically from Lot A2 (new lot)
        sale_a = Sale.objects.create(
            store=self.store_a,
            seller=self.superuser,
            total_amount=Decimal("1200000.00"),
            paid_amount=Decimal("1200000.00"),
            status=Sale.Status.PAID,
        )
        item_a = SaleItem.objects.create(
            sale=sale_a,
            product=product,
            quantity=Decimal("10.00"),
            unit_price=Decimal("120000.00"),
            purchase_price=Decimal("100000.00"),
            total_price=Decimal("1200000.00"),
        )
        StockAllocationService.allocate_sale(sale_item=item_a)
        lot_a1.refresh_from_db()
        self.assertEqual(lot_a1.remaining_quantity, Decimal("0.00"))

        # 4. Transfer 5 units from Store A (now active is Lot A2) to Store B
        transfer_item, out_allocs, in_allocs = self.transfer_lot_quantity(
            self.store_a, self.store_b, product, Decimal("5.00")
        )

        # Assert transfer out was from Lot A2
        self.assertEqual(len(out_allocs), 1)
        self.assertEqual(out_allocs[0].lot, lot_a2)
        self.assertEqual(out_allocs[0].unit_cost, Decimal("150000.00"))

        # Assert transfer in created a new distinct lot in Store B
        self.assertEqual(len(in_allocs), 1)
        lot_b_transferred = in_allocs[0].lot
        self.assertEqual(lot_b_transferred.store, self.store_b)
        self.assertEqual(lot_b_transferred.source_lot, lot_a2)
        self.assertEqual(lot_b_transferred.initial_quantity, Decimal("5.00"))
        self.assertEqual(lot_b_transferred.remaining_quantity, Decimal("5.00"))
        self.assertEqual(lot_b_transferred.purchase_price, Decimal("150000.00"))
        self.assertEqual(lot_b_transferred.selling_price, Decimal("180000.00"))

        # Assert Store B old lot is strictly untouched
        lot_b_old.refresh_from_db()
        self.assertEqual(lot_b_old.remaining_quantity, Decimal("20.00"))
        self.assertEqual(lot_b_old.purchase_price, Decimal("100000.00"))
        self.assertEqual(lot_b_old.selling_price, Decimal("120000.00"))

        # Assert Store B ProductBatch cache
        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.quantity, Decimal("25.00"))
        self.assertEqual(batch_b.purchase_price, Decimal("100000.00"))
        self.assertEqual(batch_b.selling_price, Decimal("120000.00"))

        # 5. Active selling price resolution in Store B
        # In FIFO, the active lot is the oldest lot with remaining_quantity > 0 (lot_b_old)
        active_selling_price_b = StockAllocationService.resolve_selling_price(self.store_b, product)
        self.assertEqual(active_selling_price_b, Decimal("120000.00"))

        # 6. Sale 1 in Store B: Customer buys 5 units
        # Selling price from resolver is 120 000
        sale1_data = {
            "store": self.store_b.id,
            "items": [
                {
                    "product": product.id,
                    "quantity": 5,
                    "price": active_selling_price_b,
                }
            ],
            "payments": [{"type": "cash", "amount": Decimal("600000.00")}],
        }
        sale1 = SaleService.create_sale(user=self.superuser, data=sale1_data)
        sale1_item = sale1.items.get(product=product)

        # SaleItem cost should be 100 000 (from lot_b_old)
        self.assertEqual(sale1_item.unit_price, Decimal("120000.00"))
        self.assertEqual(sale1_item.purchase_price, Decimal("100000.00"))

        lot_b_old.refresh_from_db()
        lot_b_transferred.refresh_from_db()
        self.assertEqual(lot_b_old.remaining_quantity, Decimal("15.00"))
        self.assertEqual(lot_b_transferred.remaining_quantity, Decimal("5.00"))

        # 7. Sale 2 in Store B: Selling out remaining 15 of old lot + 2 from transferred lot (total 17 units)
        sale2_data = {
            "store": self.store_b.id,
            "items": [
                {
                    "product": product.id,
                    "quantity": 17,
                    "price": Decimal("150000.00"),
                }
            ],
            "payments": [{"type": "cash", "amount": Decimal("2550000.00")}],
        }
        sale2 = SaleService.create_sale(user=self.superuser, data=sale2_data)
        sale2_item = sale2.items.get(product=product)

        # Expected cost = (15 * 100 000 + 2 * 150 000) / 17 = 1 800 000 / 17 = 105 882.35
        expected_cost = (Decimal("1800000.00") / Decimal("17.00")).quantize(Decimal("0.01"))
        self.assertEqual(sale2_item.purchase_price, expected_cost)

        # Now lot_b_old is completely 0.00, transferred lot has 3.00 remaining
        lot_b_old.refresh_from_db()
        lot_b_transferred.refresh_from_db()
        self.assertEqual(lot_b_old.remaining_quantity, Decimal("0.00"))
        self.assertEqual(lot_b_transferred.remaining_quantity, Decimal("3.00"))

        # ProductBatch in Store B has automatically transitioned to new lot's prices!
        batch_b.refresh_from_db()
        self.assertEqual(batch_b.quantity, Decimal("3.00"))
        self.assertEqual(batch_b.purchase_price, Decimal("150000.00"))
        self.assertEqual(batch_b.selling_price, Decimal("180000.00"))

        # And active price resolver now returns 180 000!
        self.assertEqual(
            StockAllocationService.resolve_selling_price(self.store_b, product),
            Decimal("180000.00"),
        )

    def test_case_b_destination_has_no_old_stock(self):
        """
        CASE B:
        Store A:
          - Lot A1 (old): 10 @ 100k / 120k (exhausted)
          - Lot A2 (new): 10 @ 150k / 180k
        Store B:
          - 0 stock.

        A'dan B'ga yangi 150k / 180k lotdan 5 dona transfer:
        1. Store B'da yangi lot yaratiladi: 5 dona @ purchase 150k, selling 180k.
        2. Store B'dagi ProductBatch darhol yangilanadi:
           - quantity = 5
           - purchase_price = 150 000
           - selling_price = 180 000
        3. Store B'da sotuv ochilganda:
           - resolve_selling_price = 180 000
           - sotuv narxi = 180 000
           - tannarx / purchase_price = 150 000
           - allocation unit_cost = 150 000
        """
        product = self.create_product("Product X - Case B")

        # 1. Setup Store A lots
        lot_a1, _ = self.create_entry_lot(self.store_a, product, Decimal("10.00"), Decimal("100000.00"), Decimal("120000.00"))
        lot_a2, _ = self.create_entry_lot(self.store_a, product, Decimal("10.00"), Decimal("150000.00"), Decimal("180000.00"))

        # Store A exhausts lot_a1
        sale_a = Sale.objects.create(
            store=self.store_a,
            seller=self.superuser,
            total_amount=Decimal("1200000.00"),
            paid_amount=Decimal("1200000.00"),
            status=Sale.Status.PAID,
        )
        item_a = SaleItem.objects.create(
            sale=sale_a,
            product=product,
            quantity=Decimal("10.00"),
            unit_price=Decimal("120000.00"),
            purchase_price=Decimal("100000.00"),
            total_price=Decimal("1200000.00"),
        )
        StockAllocationService.allocate_sale(sale_item=item_a)

        # 2. Store B has 0 stock initially
        batch_b = ProductBatch.objects.filter(store=self.store_b, product=product).first()
        self.assertTrue(batch_b is None or batch_b.quantity == Decimal("0.00"))

        # 3. Transfer 5 units from Store A to Store B
        transfer_item, out_allocs, in_allocs = self.transfer_lot_quantity(
            self.store_a, self.store_b, product, Decimal("5.00")
        )

        # 4. Assert destination lot
        self.assertEqual(len(in_allocs), 1)
        lot_b = in_allocs[0].lot
        self.assertEqual(lot_b.store, self.store_b)
        self.assertEqual(lot_b.source_lot, lot_a2)
        self.assertEqual(lot_b.initial_quantity, Decimal("5.00"))
        self.assertEqual(lot_b.remaining_quantity, Decimal("5.00"))
        self.assertEqual(lot_b.purchase_price, Decimal("150000.00"))
        self.assertEqual(lot_b.selling_price, Decimal("180000.00"))

        # 5. Assert destination ProductBatch cache
        batch_b = ProductBatch.objects.get(store=self.store_b, product=product)
        self.assertEqual(batch_b.quantity, Decimal("5.00"))
        self.assertEqual(batch_b.purchase_price, Decimal("150000.00"))
        self.assertEqual(batch_b.selling_price, Decimal("180000.00"))

        # 6. Selling price resolver returns new price
        resolved_price = StockAllocationService.resolve_selling_price(self.store_b, product)
        self.assertEqual(resolved_price, Decimal("180000.00"))

        # 7. Sale in Store B: sell 2 units
        sale_b_data = {
            "store": self.store_b.id,
            "items": [
                {
                    "product": product.id,
                    "quantity": 2,
                    "price": resolved_price,
                }
            ],
            "payments": [{"type": "cash", "amount": Decimal("360000.00")}],
        }
        sale_b = SaleService.create_sale(user=self.superuser, data=sale_b_data)
        sale_b_item = sale_b.items.get(product=product)

        # Check sale item and allocation
        self.assertEqual(sale_b_item.unit_price, Decimal("180000.00"))
        self.assertEqual(sale_b_item.purchase_price, Decimal("150000.00"))

        sale_allocs = StockAllocation.objects.filter(sale_item=sale_b_item)
        self.assertEqual(len(sale_allocs), 1)
        self.assertEqual(sale_allocs[0].quantity, Decimal("2.00"))
        self.assertEqual(sale_allocs[0].unit_cost, Decimal("150000.00"))
        self.assertEqual(sale_allocs[0].lot, lot_b)

        # Check remaining
        lot_b.refresh_from_db()
        self.assertEqual(lot_b.remaining_quantity, Decimal("3.00"))
        batch_b.refresh_from_db()
        self.assertEqual(batch_b.quantity, Decimal("3.00"))
        self.assertEqual(batch_b.purchase_price, Decimal("150000.00"))
        self.assertEqual(batch_b.selling_price, Decimal("180000.00"))
