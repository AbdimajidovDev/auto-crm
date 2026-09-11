from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from apps.contract.models import StockEntry, StockEntryItem, StockEntryReturn, StockEntryReturnItem, Supplier
from apps.inventory.models import InventorySession, StockAllocation, StockLot
from apps.products.models import Product
from apps.products.utils.barcode_utility import normalize_barcode
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.users.models import User
from apps.writeoff.models import WriteOff, WriteOffItem


class StockLotTestBase(TestCase):
    """Shared base setup for StockLot and StockAllocation unit tests."""

    _barcode_seq = 2000

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(phone_number="998909999999", full_name="Test User")
        cls.store_a = Store.objects.create(
            name="Store Alpha", phone_number="998901111111", address="Alpha", type=Store.StoreType.BASE
        )
        cls.store_b = Store.objects.create(
            name="Store Beta", phone_number="998902222222", address="Beta", type=Store.StoreType.STORE
        )
        cls.supplier = Supplier.objects.create(
            name="Main Supplier", phone_number="998903333333", address="Supplier Address"
        )

    def create_product(self, name="Test Product"):
        StockLotTestBase._barcode_seq += 1
        return Product.objects.create(
            name=name,
            barcode=normalize_barcode(f"{StockLotTestBase._barcode_seq:012d}"),
        )

    def create_stock_lot(
        self,
        store=None,
        product=None,
        supplier=None,
        lot_type=StockLot.LotType.PURCHASE,
        initial_quantity=Decimal("10.00"),
        remaining_quantity=Decimal("10.00"),
        purchase_price=Decimal("15.00"),
        source_lot=None,
        stock_entry_item=None,
    ):
        return StockLot.objects.create(
            store=store or self.store_a,
            product=product or self.product,
            supplier=supplier if supplier is not None else self.supplier,
            lot_type=lot_type,
            initial_quantity=initial_quantity,
            remaining_quantity=remaining_quantity,
            purchase_price=purchase_price,
            source_lot=source_lot,
            stock_entry_item=stock_entry_item,
        )

    def setUp(self):
        self.product = self.create_product("Default Product")


class StockLotModelTests(StockLotTestBase):
    """Tests for StockLot model constraints, invariants, and relationships."""

    def test_create_stock_lot_success(self):
        lot = self.create_stock_lot(
            initial_quantity=Decimal("50.00"),
            remaining_quantity=Decimal("50.00"),
            purchase_price=Decimal("12000.00"),
        )
        self.assertIsNotNone(lot.pk)
        self.assertEqual(lot.lot_type, StockLot.LotType.PURCHASE)
        self.assertEqual(lot.initial_quantity, Decimal("50.00"))
        self.assertEqual(lot.remaining_quantity, Decimal("50.00"))
        self.assertEqual(lot.purchase_price, Decimal("12000.00"))
        self.assertTrue(lot.is_active)
        self.assertIsNotNone(lot.created_at)

    def test_initial_quantity_gt_zero_validation(self):
        """initial_quantity <= 0 must fail validation and DB constraint."""
        with self.assertRaises((ValidationError, IntegrityError)):
            self.create_stock_lot(initial_quantity=Decimal("0.00"))

        with self.assertRaises((ValidationError, IntegrityError)):
            self.create_stock_lot(initial_quantity=Decimal("-5.00"))

    def test_remaining_quantity_gte_zero_validation(self):
        """remaining_quantity < 0 must fail validation and DB constraint."""
        with self.assertRaises((ValidationError, IntegrityError)):
            self.create_stock_lot(remaining_quantity=Decimal("-1.00"))

    def test_decimal_quantities_supported(self):
        """Decimal fraction quantities (e.g. 0.50 pair) must be properly stored."""
        lot = self.create_stock_lot(
            initial_quantity=Decimal("12.50"),
            remaining_quantity=Decimal("7.25"),
            purchase_price=Decimal("1500.75"),
        )
        lot.refresh_from_db()
        self.assertEqual(lot.initial_quantity, Decimal("12.50"))
        self.assertEqual(lot.remaining_quantity, Decimal("7.25"))
        self.assertEqual(lot.purchase_price, Decimal("1500.75"))

    def test_transfer_in_requires_source_lot(self):
        """TRANSFER_IN lot must specify a source_lot."""
        with self.assertRaises((ValidationError, IntegrityError)):
            self.create_stock_lot(
                lot_type=StockLot.LotType.TRANSFER_IN,
                source_lot=None,
            )

    def test_transfer_in_different_store_validation(self):
        """TRANSFER_IN source lot must belong to a different store."""
        source_lot = self.create_stock_lot(store=self.store_a)
        with self.assertRaises(ValidationError):
            self.create_stock_lot(
                store=self.store_a,  # Same store
                lot_type=StockLot.LotType.TRANSFER_IN,
                source_lot=source_lot,
            )

    def test_transfer_in_valid_with_different_store(self):
        """TRANSFER_IN from store_a to store_b succeeds."""
        source_lot = self.create_stock_lot(store=self.store_a)
        transfer_lot = self.create_stock_lot(
            store=self.store_b,
            lot_type=StockLot.LotType.TRANSFER_IN,
            source_lot=source_lot,
        )
        self.assertIsNotNone(transfer_lot.pk)
        self.assertEqual(transfer_lot.source_lot, source_lot)
        self.assertIn(transfer_lot, source_lot.derived_lots.all())

    def test_source_lot_must_have_same_product(self):
        """source_lot cannot belong to a different product."""
        other_product = self.create_product("Other Product")
        source_lot = self.create_stock_lot(product=other_product, store=self.store_a)
        with self.assertRaises(ValidationError):
            self.create_stock_lot(
                product=self.product,
                store=self.store_b,
                lot_type=StockLot.LotType.TRANSFER_IN,
                source_lot=source_lot,
            )

    def test_self_reference_prevention(self):
        """A lot cannot reference itself as source_lot."""
        lot = self.create_stock_lot()
        lot.source_lot = lot
        with self.assertRaises((ValidationError, IntegrityError)):
            lot.save()

    def test_foreign_key_protect_store(self):
        """Deleting Store with referenced StockLot raises ProtectedError."""
        lot = self.create_stock_lot(store=self.store_a)
        with self.assertRaises(ProtectedError):
            self.store_a.delete()

    def test_foreign_key_protect_product(self):
        """Deleting Product with referenced StockLot raises ProtectedError."""
        lot = self.create_stock_lot(product=self.product)
        with self.assertRaises(ProtectedError):
            self.product.delete()

    def test_foreign_key_protect_supplier(self):
        """Deleting Supplier with referenced StockLot raises ProtectedError."""
        lot = self.create_stock_lot(supplier=self.supplier)
        with self.assertRaises(ProtectedError):
            self.supplier.delete()

    def test_model_indexes_and_constraints_present(self):
        """Verify presence of all required indexes and check constraints on StockLot."""
        constraint_names = {c.name for c in StockLot._meta.constraints}
        self.assertIn("stock_lot_initial_qty_gt_zero", constraint_names)
        self.assertIn("stock_lot_remaining_qty_gte_zero", constraint_names)
        self.assertIn("stock_lot_transfer_in_has_source", constraint_names)
        self.assertIn("stock_lot_prevent_self_reference", constraint_names)

        index_names = {idx.name for idx in StockLot._meta.indexes}
        self.assertIn("stock_lot_store_prod_rem_idx", index_names)
        self.assertIn("stock_lot_supplier_prod_idx", index_names)
        self.assertIn("stock_lot_fifo_partial_idx", index_names)
        self.assertIn("stock_lot_created_at_idx", index_names)


class StockAllocationModelTests(StockLotTestBase):
    """Tests for StockAllocation model constraints, invariants, source exclusivity, and immutability."""

    def setUp(self):
        super().setUp()
        self.lot = self.create_stock_lot()
        self.inventory_session = InventorySession.objects.create(
            store=self.store_a,
            started_by=self.user,
        )

        # Fixture objects for foreign keys
        self.sale = Sale.objects.create(
            store=self.store_a,
            seller=self.user,
            total_amount=Decimal("100.00"),
            paid_amount=Decimal("100.00"),
            status=Sale.Status.PAID,
        )
        self.sale_item = SaleItem.objects.create(
            sale=self.sale,
            product=self.product,
            quantity=Decimal("2.00"),
            unit_price=Decimal("50.00"),
            total_price=Decimal("100.00"),
        )

        self.sale_return = SaleReturn.objects.create(
            sale=self.sale,
            store=self.store_a,
            seller=self.user,
            total_refund=Decimal("50.00"),
        )
        self.sale_return_item = SaleReturnItem.objects.create(
            sale_return=self.sale_return,
            sale_item=self.sale_item,
            product=self.product,
            quantity=Decimal("1.00"),
            unit_price=Decimal("50.00"),
            total_price=Decimal("50.00"),
        )

        self.write_off = WriteOff.objects.create(
            store=self.store_a,
            created_by=self.user,
            reason=WriteOff.Reason.DAMAGED,
        )
        self.write_off_item = WriteOffItem.objects.create(
            write_off=self.write_off,
            product=self.product,
            quantity=Decimal("1.00"),
            purchase_price=Decimal("15.00"),
            selling_price=Decimal("50.00"),
        )

        self.stock_transfer = StockTransfer.objects.create(
            from_store=self.store_a,
            to_store=self.store_b,
            status=StockTransfer.Status.APPROVED,
            created_by=self.user,
        )
        self.transfer_item = StockTransferItem.objects.create(
            stock_transfer=self.stock_transfer,
            product=self.product,
            quantity=Decimal("2.00"),
            purchase_price=Decimal("15.00"),
            selling_price=Decimal("50.00"),
        )

        self.stock_entry = StockEntry.objects.create(
            supplier=self.supplier,
            store=self.store_a,
            total_amount=Decimal("150.00"),
            cash_amount=Decimal("150.00"),
            created_by=self.user,
        )
        self.stock_entry_item = StockEntryItem.objects.create(
            entry=self.stock_entry,
            product=self.product,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("15.00"),
            selling_price=Decimal("50.00"),
        )
        self.stock_return = StockEntryReturn.objects.create(
            entry=self.stock_entry,
            total_amount=Decimal("30.00"),
            created_by=self.user,
        )
        self.supplier_return_item = StockEntryReturnItem.objects.create(
            stock_return=self.stock_return,
            entry_item=self.stock_entry_item,
            product=self.product,
            quantity=Decimal("2.00"),
            purchase_price=Decimal("15.00"),
            amount=Decimal("30.00"),
        )

    def test_create_sale_allocation_success(self):
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("15.00"),
            sale_item=self.sale_item,
        )
        self.assertIsNotNone(alloc.pk)
        self.assertEqual(alloc.movement_type, StockAllocation.MovementType.SALE)
        self.assertEqual(alloc.direction, StockAllocation.Direction.OUT)
        self.assertEqual(alloc.quantity, Decimal("2.00"))
        self.assertEqual(alloc.sale_item, self.sale_item)

    def test_create_sale_return_allocation_success(self):
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE_RETURN,
            direction=StockAllocation.Direction.IN,
            quantity=Decimal("1.00"),
            unit_cost=Decimal("15.00"),
            sale_return_item=self.sale_return_item,
        )
        self.assertIsNotNone(alloc.pk)
        self.assertEqual(alloc.direction, StockAllocation.Direction.IN)
        self.assertEqual(alloc.sale_return_item, self.sale_return_item)

    def test_create_write_off_allocation_success(self):
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.WRITE_OFF,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("1.00"),
            unit_cost=Decimal("15.00"),
            write_off_item=self.write_off_item,
        )
        self.assertIsNotNone(alloc.pk)
        self.assertEqual(alloc.write_off_item, self.write_off_item)

    def test_create_transfer_out_allocation_success(self):
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.TRANSFER_OUT,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("15.00"),
            transfer_item=self.transfer_item,
        )
        self.assertIsNotNone(alloc.pk)
        self.assertEqual(alloc.direction, StockAllocation.Direction.OUT)
        self.assertEqual(alloc.transfer_item, self.transfer_item)

    def test_create_transfer_in_allocation_success(self):
        transfer_lot = self.create_stock_lot(
            store=self.store_b,
            lot_type=StockLot.LotType.TRANSFER_IN,
            source_lot=self.lot,
        )
        alloc = StockAllocation.objects.create(
            lot=transfer_lot,
            movement_type=StockAllocation.MovementType.TRANSFER_IN,
            direction=StockAllocation.Direction.IN,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("15.00"),
            transfer_item=self.transfer_item,
        )
        self.assertIsNotNone(alloc.pk)
        self.assertEqual(alloc.direction, StockAllocation.Direction.IN)

    def test_create_supplier_return_allocation_success(self):
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SUPPLIER_RETURN,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("15.00"),
            supplier_return_item=self.supplier_return_item,
        )
        self.assertIsNotNone(alloc.pk)
        self.assertEqual(alloc.direction, StockAllocation.Direction.OUT)
        self.assertEqual(alloc.supplier_return_item, self.supplier_return_item)

    def test_create_inventory_excess_allocation_success(self):
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.INVENTORY_EXCESS,
            direction=StockAllocation.Direction.IN,
            quantity=Decimal("3.00"),
            unit_cost=Decimal("15.00"),
            inventory_session=self.inventory_session,
            description="Inventory audit found excess stock",
        )
        self.assertIsNotNone(alloc.pk)
        self.assertEqual(alloc.direction, StockAllocation.Direction.IN)
        self.assertEqual(alloc.inventory_session, self.inventory_session)
        self.assertIsNone(alloc.sale_item)
        self.assertIsNone(alloc.sale_return_item)
        self.assertIsNone(alloc.write_off_item)
        self.assertIsNone(alloc.transfer_item)
        self.assertIsNone(alloc.supplier_return_item)

    def test_create_inventory_shortage_allocation_success(self):
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.INVENTORY_SHORTAGE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("15.00"),
            inventory_session=self.inventory_session,
            description="Inventory audit shortage",
        )
        self.assertIsNotNone(alloc.pk)
        self.assertEqual(alloc.direction, StockAllocation.Direction.OUT)
        self.assertEqual(alloc.inventory_session, self.inventory_session)

    def test_quantity_gt_zero_validation(self):
        """quantity <= 0 must fail validation and DB constraint."""
        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.SALE,
                direction=StockAllocation.Direction.OUT,
                quantity=Decimal("0.00"),
                sale_item=self.sale_item,
            )

        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.SALE,
                direction=StockAllocation.Direction.OUT,
                quantity=Decimal("-1.00"),
                sale_item=self.sale_item,
            )

    def test_decimal_quantities_supported(self):
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("1.50"),
            unit_cost=Decimal("15.75"),
            sale_item=self.sale_item,
        )
        alloc.refresh_from_db()
        self.assertEqual(alloc.quantity, Decimal("1.50"))
        self.assertEqual(alloc.unit_cost, Decimal("15.75"))

    def test_direction_mismatch_raises_error(self):
        """SALE cannot have direction='in'; SALE_RETURN cannot have direction='out'."""
        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.SALE,
                direction=StockAllocation.Direction.IN,  # INVALID
                quantity=Decimal("1.00"),
                sale_item=self.sale_item,
            )

        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.SALE_RETURN,
                direction=StockAllocation.Direction.OUT,  # INVALID
                quantity=Decimal("1.00"),
                sale_return_item=self.sale_return_item,
            )

    def test_source_exclusivity_sale_missing_item(self):
        """SALE without sale_item must fail."""
        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.SALE,
                direction=StockAllocation.Direction.OUT,
                quantity=Decimal("1.00"),
                sale_item=None,
            )

    def test_source_exclusivity_multiple_items_rejected(self):
        """Setting both sale_item and write_off_item must fail."""
        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.SALE,
                direction=StockAllocation.Direction.OUT,
                quantity=Decimal("1.00"),
                sale_item=self.sale_item,
                write_off_item=self.write_off_item,
            )

    def test_source_exclusivity_inventory_shortage_with_item_rejected(self):
        """INVENTORY_SHORTAGE cannot have domain item foreign keys."""
        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.INVENTORY_SHORTAGE,
                direction=StockAllocation.Direction.OUT,
                quantity=Decimal("1.00"),
                inventory_session=self.inventory_session,
                sale_item=self.sale_item,  # INVALID for inventory shortage
            )

    def test_foreign_key_protect_lot(self):
        """Deleting StockLot with existing StockAllocation raises ProtectedError."""
        StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("1.00"),
            sale_item=self.sale_item,
        )
        with self.assertRaises(ProtectedError):
            self.lot.delete()

    def test_foreign_key_protect_sale_item(self):
        """Deleting SaleItem with existing StockAllocation raises ProtectedError."""
        StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("1.00"),
            sale_item=self.sale_item,
        )
        with self.assertRaises(ProtectedError):
            self.sale_item.delete()

    def test_immutability_update_prevented(self):
        """Updating an existing StockAllocation record must be blocked."""
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("1.00"),
            sale_item=self.sale_item,
        )
        alloc.quantity = Decimal("5.00")
        with self.assertRaises(ValidationError):
            alloc.save()

    def test_immutability_delete_prevented(self):
        """Deleting a StockAllocation record directly must be blocked."""
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("1.00"),
            sale_item=self.sale_item,
        )
        with self.assertRaises(ValidationError):
            alloc.delete()

    def test_model_indexes_and_constraints_present(self):
        """Verify presence of all required indexes and check constraints on StockAllocation."""
        constraint_names = {c.name for c in StockAllocation._meta.constraints}
        self.assertIn("stock_allocation_qty_gt_zero", constraint_names)
        self.assertIn("stock_allocation_movement_direction_match", constraint_names)
        self.assertIn("stock_allocation_source_exclusivity", constraint_names)
        self.assertIn("stock_alloc_reversal_only_sale_return", constraint_names)
        self.assertIn("stock_alloc_prevent_self_reversal", constraint_names)
        self.assertIn("stock_alloc_inventory_session_match", constraint_names)

        index_names = {idx.name for idx in StockAllocation._meta.indexes}
        self.assertIn("stock_alloc_lot_created_idx", index_names)
        self.assertIn("stock_alloc_mov_type_idx", index_names)
        self.assertIn("stock_alloc_sale_item_idx", index_names)
        self.assertIn("stock_alloc_return_item_idx", index_names)
        self.assertIn("stock_alloc_woff_item_idx", index_names)
        self.assertIn("stock_alloc_trans_item_idx", index_names)
        self.assertIn("stock_alloc_sup_ret_idx", index_names)
        self.assertIn("stock_alloc_rep_idx", index_names)

    def test_reversal_of_valid_sale_allocation(self):
        """SALE_RETURN with valid reversal_of referencing a SALE OUT allocation succeeds."""
        sale_alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("15.00"),
            sale_item=self.sale_item,
        )
        return_alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE_RETURN,
            direction=StockAllocation.Direction.IN,
            quantity=Decimal("1.00"),
            unit_cost=Decimal("15.00"),
            sale_return_item=self.sale_return_item,
            reversal_of=sale_alloc,
        )
        self.assertIsNotNone(return_alloc.pk)
        self.assertEqual(return_alloc.reversal_of, sale_alloc)
        self.assertIn(return_alloc, sale_alloc.reversals.all())

    def test_reversal_of_pointing_to_non_out_rejected(self):
        """reversal_of cannot point to an IN allocation or another SALE_RETURN."""
        sale_return_alloc_1 = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE_RETURN,
            direction=StockAllocation.Direction.IN,
            quantity=Decimal("1.00"),
            unit_cost=Decimal("15.00"),
            sale_return_item=self.sale_return_item,
        )
        with self.assertRaises(ValidationError):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.SALE_RETURN,
                direction=StockAllocation.Direction.IN,
                quantity=Decimal("1.00"),
                unit_cost=Decimal("15.00"),
                sale_return_item=self.sale_return_item,
                reversal_of=sale_return_alloc_1,
            )

    def test_reversal_of_on_sale_rejected(self):
        """Non-return allocations (e.g. SALE) cannot have reversal_of."""
        sale_alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("15.00"),
            sale_item=self.sale_item,
        )
        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.SALE,
                direction=StockAllocation.Direction.OUT,
                quantity=Decimal("1.00"),
                unit_cost=Decimal("15.00"),
                sale_item=self.sale_item,
                reversal_of=sale_alloc,
            )

    def test_reversal_of_self_reference_rejected(self):
        """An allocation cannot have reversal_of pointing to itself."""
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE_RETURN,
            direction=StockAllocation.Direction.IN,
            quantity=Decimal("1.00"),
            unit_cost=Decimal("15.00"),
            sale_return_item=self.sale_return_item,
        )
        alloc.reversal_of = alloc
        with self.assertRaises(ValidationError):
            alloc.clean()
        with self.assertRaises(IntegrityError):
            StockAllocation.objects.filter(id=alloc.id).update(reversal_of=alloc)

    def test_reversal_of_different_lot_rejected(self):
        """reversal_of must belong to the exact same StockLot."""
        other_lot = self.create_stock_lot()
        sale_alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("15.00"),
            sale_item=self.sale_item,
        )
        with self.assertRaises(ValidationError):
            StockAllocation.objects.create(
                lot=other_lot,
                movement_type=StockAllocation.MovementType.SALE_RETURN,
                direction=StockAllocation.Direction.IN,
                quantity=Decimal("1.00"),
                unit_cost=Decimal("15.00"),
                sale_return_item=self.sale_return_item,
                reversal_of=sale_alloc,
            )

    def test_reversal_of_null_on_sale_valid(self):
        """Regular SALE allocations with reversal_of=None are valid."""
        sale_alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("15.00"),
            sale_item=self.sale_item,
            reversal_of=None,
        )
        self.assertIsNotNone(sale_alloc.pk)
        self.assertIsNone(sale_alloc.reversal_of)

    def test_reversal_of_protect_on_delete(self):
        """Deleting an original allocation that has reversals raises ProtectedError."""
        sale_alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("15.00"),
            sale_item=self.sale_item,
        )
        StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.SALE_RETURN,
            direction=StockAllocation.Direction.IN,
            quantity=Decimal("1.00"),
            unit_cost=Decimal("15.00"),
            sale_return_item=self.sale_return_item,
            reversal_of=sale_alloc,
        )
        with self.assertRaises(ProtectedError):
            StockAllocation.objects.filter(id=sale_alloc.id).delete()

    def test_inventory_shortage_with_session_valid(self):
        """INVENTORY_SHORTAGE with inventory_session succeeds."""
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.INVENTORY_SHORTAGE,
            direction=StockAllocation.Direction.OUT,
            quantity=Decimal("1.00"),
            unit_cost=Decimal("15.00"),
            inventory_session=self.inventory_session,
        )
        self.assertIsNotNone(alloc.pk)
        self.assertEqual(alloc.inventory_session, self.inventory_session)

    def test_inventory_excess_with_session_valid(self):
        """INVENTORY_EXCESS with inventory_session succeeds."""
        alloc = StockAllocation.objects.create(
            lot=self.lot,
            movement_type=StockAllocation.MovementType.INVENTORY_EXCESS,
            direction=StockAllocation.Direction.IN,
            quantity=Decimal("2.00"),
            unit_cost=Decimal("15.00"),
            inventory_session=self.inventory_session,
        )
        self.assertIsNotNone(alloc.pk)
        self.assertEqual(alloc.inventory_session, self.inventory_session)

    def test_inventory_shortage_without_session_rejected(self):
        """INVENTORY_SHORTAGE without inventory_session fails validation and DB constraint."""
        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.INVENTORY_SHORTAGE,
                direction=StockAllocation.Direction.OUT,
                quantity=Decimal("1.00"),
                unit_cost=Decimal("15.00"),
                inventory_session=None,
            )

    def test_inventory_excess_without_session_rejected(self):
        """INVENTORY_EXCESS without inventory_session fails validation and DB constraint."""
        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.INVENTORY_EXCESS,
                direction=StockAllocation.Direction.IN,
                quantity=Decimal("1.00"),
                unit_cost=Decimal("15.00"),
                inventory_session=None,
            )

    def test_sale_with_inventory_session_rejected(self):
        """SALE with inventory_session fails validation and DB constraint."""
        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.SALE,
                direction=StockAllocation.Direction.OUT,
                quantity=Decimal("1.00"),
                unit_cost=Decimal("15.00"),
                sale_item=self.sale_item,
                inventory_session=self.inventory_session,
            )

    def test_sale_return_with_inventory_session_rejected(self):
        """SALE_RETURN with inventory_session fails validation and DB constraint."""
        with self.assertRaises((ValidationError, IntegrityError)):
            StockAllocation.objects.create(
                lot=self.lot,
                movement_type=StockAllocation.MovementType.SALE_RETURN,
                direction=StockAllocation.Direction.IN,
                quantity=Decimal("1.00"),
                unit_cost=Decimal("15.00"),
                sale_return_item=self.sale_return_item,
                inventory_session=self.inventory_session,
            )
