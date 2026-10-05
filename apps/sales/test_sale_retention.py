"""
Targeted regression test suite for Data Retention and Purge Fix (P1 #2).

Test coverage:
1. Test 1 — Expired soft-deleted sale with StockAllocation is NEVER hard-deleted by purge.
2. Test 2 — Sale -> SaleItem -> StockAllocation lineage remains 100% intact.
3. Test 3 — SaleReturn -> SaleReturnItem -> StockAllocation (reversal_of) lineage remains intact.
4. Test 4 — Archive List API (GET /api/sales/archive/) returns 200 OK without 500 errors for expired sales.
5. Test 5 — Management command `purge_deleted_sales` executes safely without destroying ledger.
6. Test 6 — Active (non-deleted) sales are never touched or archived by purge.
7. Test 7 — Accounting reports continue to exclude soft-deleted sales as designed.
8. Test 8 — FIFO and inventory lot integrity remain consistent before and after purge.
9. Test 9 — Frontend archive response contract (results, retention_days, days_left, fields) preserved.
10. Test 10 — BulkDeleteAPIView succeeds without ProtectedError even when expired sales exist in archive.
11. Test 11 — RestoreAPIView successfully restores soft-deleted sales older than 30 days.
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.inventory.models import StockAllocation, StockLot
from apps.inventory.services.stock_allocation_service import StockAllocationService
from apps.products.models import Category, Product, ProductBatch
from apps.reports.services.supplier_sales_report_service import SupplierSalesReportService
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.sales.services.sales_services import SaleService
from apps.sales.views.sale_view import purge_expired_deleted_sales
from apps.store.models import Store, StoreUser
from apps.users.models.customers import Customer
from apps.users.models.user import User


class SaleDataRetentionTests(APITestCase):
    """Tests for Indefinite Soft-Delete Retention Policy and Purge Safety."""

    def setUp(self):
        self.store = Store.objects.create(name="Toshkent Filial", phone_number="+998901234567")
        self.admin = User.objects.create_superuser(
            phone_number="+998901112233",
            email="superadmin@autocrm.uz",
            password="adminpassword123",
        )
        StoreUser.objects.create(user=self.admin, store=self.store, is_active=True)

        self.customer = Customer.objects.create(
            full_name="Alisher Navoiy",
            phone_number="+998909876543",
        )
        self.category = Category.objects.create(name="Ehtiyot qismlar")
        self.product = Product.objects.create(
            name="Tormoz Kolodkasi",
            category=self.category,
            sku="SKU-KOLODKA-1",
            barcode="7770001",
            status=Product.ProductStatus.ACTIVE,
        )

        # Create lot with 20 units
        self.lot = StockLot.objects.create(
            store=self.store,
            product=self.product,
            initial_quantity=Decimal("20.00"),
            remaining_quantity=Decimal("20.00"),
            purchase_price=Decimal("100000.00"),
        )
        with transaction.atomic():
            self.batch = StockAllocationService._sync_product_batch(self.store, self.product)

    def _create_sale_with_allocation(self, quantity=Decimal("5.00"), unit_price=Decimal("150000.00")):
        """Helper to create a sale with valid StockAllocation."""
        sale_data = {
            "store": self.store.id,
            "customer": self.customer.id,
            "items": [
                {"product": self.product.id, "quantity": quantity, "price": unit_price},
            ],
            "payments": [
                {"type": "cash", "amount": quantity * unit_price},
            ],
        }
        return SaleService.create_sale(user=self.admin, data=sale_data)

    def test_expired_deleted_sale_not_hard_deleted(self):
        """Test 1: Expired soft-deleted sale with StockAllocation is NEVER hard-deleted by purge."""
        sale = self._create_sale_with_allocation(quantity=Decimal("4.00"))
        sale_id = sale.id

        # Soft delete the sale 40 days ago (> 30 days)
        past_dt = timezone.now() - timedelta(days=40)
        Sale.all_objects.filter(id=sale_id).update(deleted_at=past_dt)

        purged_count = purge_expired_deleted_sales()

        self.assertEqual(purged_count, 0)
        # Sale must still exist in DB with deleted_at preserved
        sale_db = Sale.all_objects.filter(id=sale_id).first()
        self.assertIsNotNone(sale_db)
        self.assertEqual(sale_db.deleted_at, past_dt)

    def test_sale_lineage_integrity_after_purge(self):
        """Test 2: Sale -> SaleItem -> StockAllocation lineage remains 100% intact."""
        sale = self._create_sale_with_allocation(quantity=Decimal("3.00"))
        sale_item = sale.items.first()

        past_dt = timezone.now() - timedelta(days=60)
        Sale.all_objects.filter(id=sale.id).update(deleted_at=past_dt)

        purge_expired_deleted_sales()

        # Check SaleItem and StockAllocation still exist and link correctly
        self.assertTrue(SaleItem.objects.filter(id=sale_item.id).exists())
        allocations = list(StockAllocation.objects.filter(sale_item=sale_item))
        self.assertEqual(len(allocations), 1)
        alloc = allocations[0]
        self.assertEqual(alloc.movement_type, StockAllocation.MovementType.SALE)
        self.assertEqual(alloc.direction, StockAllocation.Direction.OUT)
        self.assertEqual(alloc.quantity, Decimal("3.00"))
        self.assertEqual(alloc.lot_id, self.lot.id)

    def test_return_lineage_integrity_after_purge(self):
        """Test 3: SaleReturn -> SaleReturnItem -> StockAllocation (reversal_of) lineage intact."""
        sale = self._create_sale_with_allocation(quantity=Decimal("5.00"))
        sale_item = sale.items.first()

        # Create return of 2 units
        sale_return = SaleReturn.objects.create(
            sale=sale,
            store=self.store,
            customer=self.customer,
            seller=self.admin,
            total_refund=Decimal("300000.00"),
        )
        return_item = SaleReturnItem.objects.create(
            sale_return=sale_return,
            sale_item=sale_item,
            product=self.product,
            quantity=Decimal("2.00"),
            unit_price=Decimal("150000.00"),
            total_price=Decimal("300000.00"),
        )
        StockAllocationService.reverse_sale_return(sale_return_item=return_item)

        # Soft delete sale 45 days ago
        past_dt = timezone.now() - timedelta(days=45)
        Sale.all_objects.filter(id=sale.id).update(deleted_at=past_dt)

        purge_expired_deleted_sales()

        # SaleReturn, SaleReturnItem, and reversal allocations must exist intact
        self.assertTrue(SaleReturn.objects.filter(id=sale_return.id).exists())
        self.assertTrue(SaleReturnItem.objects.filter(id=return_item.id).exists())
        return_alloc = StockAllocation.objects.get(sale_return_item=return_item)
        self.assertEqual(return_alloc.movement_type, StockAllocation.MovementType.SALE_RETURN)
        self.assertEqual(return_alloc.direction, StockAllocation.Direction.IN)
        self.assertIsNotNone(return_alloc.reversal_of)
        self.assertEqual(return_alloc.reversal_of.sale_item_id, sale_item.id)

    def test_archive_api_no_500_with_expired_sales(self):
        """Test 4: Archive List API returns 200 OK without 500 errors for expired sales."""
        sale = self._create_sale_with_allocation(quantity=Decimal("2.00"))
        past_dt = timezone.now() - timedelta(days=35)
        Sale.all_objects.filter(id=sale.id).update(deleted_at=past_dt)

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/sales/archive/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn("results", data)
        self.assertIsNone(data.get("retention_days"))
        results = data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], sale.id)
        self.assertIsNone(results[0]["days_left"])

    def test_purge_command_preserves_historical_ledger(self):
        """Test 5: Management command `purge_deleted_sales` executes safely without destroying ledger."""
        sale = self._create_sale_with_allocation(quantity=Decimal("1.00"))
        past_dt = timezone.now() - timedelta(days=50)
        Sale.all_objects.filter(id=sale.id).update(deleted_at=past_dt)

        out = StringIO()
        call_command("purge_deleted_sales", stdout=out)

        output_str = out.getvalue()
        self.assertIn("Indefinite Retention", output_str)
        self.assertIn("0 ta sotuv", output_str)
        self.assertTrue(Sale.all_objects.filter(id=sale.id).exists())

    def test_active_sales_unaffected(self):
        """Test 6: Active (non-deleted) sales are never touched or archived by purge."""
        active_sale = self._create_sale_with_allocation(quantity=Decimal("2.00"))

        purge_expired_deleted_sales()

        active_sale.refresh_from_db()
        self.assertIsNone(active_sale.deleted_at)
        self.assertEqual(Sale.objects.filter(id=active_sale.id).count(), 1)

    def test_accounting_reports_exclude_deleted_sales(self):
        """Test 7: Accounting reports continue to exclude soft-deleted sales as designed."""
        # 1 active sale and 1 soft-deleted sale
        active_sale = self._create_sale_with_allocation(quantity=Decimal("2.00"), unit_price=Decimal("150000.00"))
        deleted_sale = self._create_sale_with_allocation(quantity=Decimal("3.00"), unit_price=Decimal("150000.00"))

        past_dt = timezone.now() - timedelta(days=40)
        Sale.all_objects.filter(id=deleted_sale.id).update(deleted_at=past_dt)

        purge_expired_deleted_sales()

        # Check SupplierSalesReportService excludes the deleted sale
        start_date = (timezone.now() - timedelta(days=50)).date().isoformat()
        end_date = (timezone.now() + timedelta(days=1)).date().isoformat()
        _, rows, _, _ = SupplierSalesReportService.build_report(
            params={
                "report_type": "supplier_sales",
                "store_id": self.store.id,
                "date_from": start_date,
                "date_to": end_date,
            },
            user=self.admin,
        )

        # Only active sale quantity (2.00) should be included, not deleted sale quantity (3.00)
        total_sold = sum(Decimal(str(r["sold_qty"])) for r in rows)
        self.assertEqual(total_sold, Decimal("2.00"))

    def test_fifo_inventory_history_integrity(self):
        """Test 8: FIFO and inventory lot integrity remain consistent before and after purge."""
        initial_remaining = self.lot.remaining_quantity  # 20.00
        sale = self._create_sale_with_allocation(quantity=Decimal("4.00"))

        self.lot.refresh_from_db()
        self.assertEqual(self.lot.remaining_quantity, initial_remaining - Decimal("4.00"))

        past_dt = timezone.now() - timedelta(days=90)
        Sale.all_objects.filter(id=sale.id).update(deleted_at=past_dt)

        purge_expired_deleted_sales()

        self.lot.refresh_from_db()
        # Remaining quantity must remain 16.00 (not arbitrarily increased or zeroed)
        self.assertEqual(self.lot.remaining_quantity, Decimal("16.00"))
        # Sum of allocations for lot must still equal 4.00
        total_alloc = StockAllocation.objects.filter(lot=self.lot).count()
        self.assertEqual(total_alloc, 1)

    def test_frontend_archive_contract_preserved(self):
        """Test 9: Frontend archive response contract preserved with expected keys."""
        sale = self._create_sale_with_allocation(quantity=Decimal("2.00"))
        Sale.all_objects.filter(id=sale.id).update(deleted_at=timezone.now() - timedelta(days=10))

        self.client.force_authenticate(user=self.admin)
        res = self.client.get("/api/sales/archive/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        payload = res.json()

        # Root contract
        self.assertIn("results", payload)
        self.assertIn("retention_days", payload)

        # Item contract
        item = payload["results"][0]
        expected_keys = {
            "id",
            "store_name",
            "customer_name",
            "total_amount",
            "paid_amount",
            "created_at",
            "deleted_at",
            "days_left",
        }
        self.assertTrue(expected_keys.issubset(set(item.keys())))

    def test_bulk_delete_api_with_expired_archive_sales(self):
        """Test 10: BulkDeleteAPIView succeeds without ProtectedError even when expired sales exist."""
        # Pre-existing expired sale
        old_sale = self._create_sale_with_allocation(quantity=Decimal("1.00"))
        Sale.all_objects.filter(id=old_sale.id).update(deleted_at=timezone.now() - timedelta(days=45))

        # New sale to delete
        new_sale = self._create_sale_with_allocation(quantity=Decimal("2.00"))

        self.client.force_authenticate(user=self.admin)
        response = self.client.post("/api/sales/bulk-delete/", {"ids": [new_sale.id]}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"archived": 1})
        new_sale.refresh_from_db()
        self.assertIsNotNone(new_sale.deleted_at)

    def test_restore_api_for_expired_sale(self):
        """Test 11: RestoreAPIView successfully restores soft-deleted sales older than 30 days."""
        sale = self._create_sale_with_allocation(quantity=Decimal("2.00"))
        past_dt = timezone.now() - timedelta(days=60)
        Sale.all_objects.filter(id=sale.id).update(deleted_at=past_dt)

        self.client.force_authenticate(user=self.admin)
        response = self.client.post("/api/sales/archive/restore/", {"ids": [sale.id]}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"restored": 1})

        sale.refresh_from_db()
        self.assertIsNone(sale.deleted_at)
        # Restored sale immediately visible in standard Sale.objects queries
        self.assertTrue(Sale.objects.filter(id=sale.id).exists())
