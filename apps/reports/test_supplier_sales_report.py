from datetime import date, datetime, timedelta
from decimal import Decimal
import io

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.contract.models import Supplier
from apps.contract.services.stock_entry_service import StockEntryService
from apps.inventory.models import StockAllocation, StockLot
from apps.products.models import Brand, Category, Product, ProductBatch
from apps.reports.services.report_builder import ReportBuilderService
from apps.reports.services.supplier_sales_report_service import SupplierSalesReportService
from apps.reports.views.report_builder_view import (
    ReportBuilderExportAPIView,
    ReportBuilderGenerateAPIView,
)
from apps.sales.models import Sale, SaleItem
from apps.sales.services.sale_return_service import SaleReturnService
from apps.sales.services.sales_services import SaleService
from apps.store.models import Store, StoreUser
from apps.users.models.customers import Customer
from apps.users.models.role import Role
from apps.users.models.user import User


class SupplierSalesReportTests(TestCase):
    """
    Focused tests for Phase 1.8 Step 5: Supplier Sales Report.
    Validates all 25 core business correctness cases.
    """

    @classmethod
    def setUpTestData(cls):
        # Stores
        cls.store1 = Store.objects.create(name="Store Alpha", address="Alpha Address", phone_number="+998901111111")
        cls.store2 = Store.objects.create(name="Store Beta", address="Beta Address", phone_number="+998902222222")

        # Roles
        cls.role_viewer = Role.objects.create(name="Supplier Viewer", permissions=["reports.view", "reports.supplier_sales.view"])
        cls.role_exporter = Role.objects.create(name="Supplier Exporter", permissions=["reports.view", "reports.supplier_sales.view", "reports.supplier_sales.export"])
        cls.role_no_view = Role.objects.create(name="No View Role", permissions=["reports.view"])
        cls.role_no_reports = Role.objects.create(name="No Reports Role", permissions=[])

        # Users
        cls.admin = User.objects.create(phone_number="+998900000001", full_name="Super Admin", is_superuser=True, is_staff=True)
        cls.seller_user = User.objects.create(phone_number="+998900000002", full_name="Seller Sam", is_superuser=False)
        cls.store1_mgr = User.objects.create(phone_number="+998900000003", full_name="Store 1 Mgr", role=cls.role_viewer, is_superuser=False)
        cls.store2_mgr = User.objects.create(phone_number="+998900000004", full_name="Store 2 Mgr", role=cls.role_viewer, is_superuser=False)
        cls.export_user = User.objects.create(phone_number="+998900000005", full_name="Export User", role=cls.role_exporter, is_superuser=False)
        cls.no_perm_user = User.objects.create(phone_number="+998900000006", full_name="No Perm User", role=cls.role_no_view, is_superuser=False)

        StoreUser.objects.create(user=cls.store1_mgr, store=cls.store1, is_active=True)
        StoreUser.objects.create(user=cls.store2_mgr, store=cls.store2, is_active=True)
        StoreUser.objects.create(user=cls.export_user, store=cls.store1, is_active=True)
        StoreUser.objects.create(user=cls.seller_user, store=cls.store1, is_active=True)
        StoreUser.objects.create(user=cls.admin, store=cls.store1, is_active=True)
        StoreUser.objects.create(user=cls.admin, store=cls.store2, is_active=True)

        # Categories & Brands
        cls.cat_brakes = Category.objects.create(name="Brakes")
        cls.cat_filters = Category.objects.create(name="Filters")
        cls.brand_bosch = Brand.objects.create(name="Bosch")
        cls.brand_brembo = Brand.objects.create(name="Brembo")

        # Products
        cls.product1 = Product.objects.create(
            name="Brake Pads Front",
            barcode="4781001000018",
            sku="BP-001",
            category=cls.cat_brakes,
            brand=cls.brand_bosch,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.product2 = Product.objects.create(
            name="Oil Filter Pro",
            barcode="4781001000025",
            sku="OF-002",
            category=cls.cat_filters,
            brand=cls.brand_brembo,
            status=Product.ProductStatus.ACTIVE,
        )

        # Suppliers & Customer
        cls.supplier1 = Supplier.objects.create(name="Global Auto Supply")
        cls.supplier2 = Supplier.objects.create(name="Premium Parts Co")
        cls.customer = Customer.objects.create(full_name="Akmal Saidov", phone_number="+998901234567")

        cls.factory = APIRequestFactory()

    # 1. Single supplier sale
    def test_01_single_supplier_sale(self):
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("1000.00"),
        )
        sale = SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("5.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("750.00")}]},
        )
        cols, rows, _, summary = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id})
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["supplier"], self.supplier1.name)
        self.assertEqual(row["product"], self.product1.name)
        self.assertEqual(row["sold_qty"], Decimal("5.00"))
        self.assertEqual(row["returned_qty"], Decimal("0.00"))
        self.assertEqual(row["net_sold_qty"], Decimal("5.00"))
        self.assertEqual(Decimal(row["revenue"]), Decimal("750.00"))

    # 2. Multiple suppliers for same product
    def test_02_multiple_suppliers_for_same_product(self):
        # Supplier 1: 5 units
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("5.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("500.00"),
        )
        # Supplier 2: 5 units
        StockEntryService.create_entry(
            supplier=self.supplier2, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("5.00"), "purchase_price": Decimal("110.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("550.00"),
        )
        # Sale 1: 5 units (takes supplier 1)
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("5.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("750.00")}]},
        )
        # Sale 2: 2 units (takes supplier 2)
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]},
        )
        cols, rows, _, summary = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "group_mode": "period"})
        # Must produce 2 rows: one for supplier1 (5 sold), one for supplier2 (2 sold)
        self.assertEqual(len(rows), 2)
        smap = {r["supplier"]: r for r in rows}
        self.assertIn(self.supplier1.name, smap)
        self.assertIn(self.supplier2.name, smap)
        self.assertEqual(smap[self.supplier1.name]["sold_qty"], Decimal("5.00"))
        self.assertEqual(smap[self.supplier2.name]["sold_qty"], Decimal("2.00"))

    # 3. Multi-lot SaleItem
    def test_03_multi_lot_sale_item(self):
        # Entry 1 (Supplier 1): 4 units
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("4.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("400.00"),
        )
        # Entry 2 (Supplier 2): 6 units
        StockEntryService.create_entry(
            supplier=self.supplier2, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("6.00"), "purchase_price": Decimal("110.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("660.00"),
        )
        # Single SaleItem for 7 units (consumes 4 from supplier1 and 3 from supplier2)
        sale = SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("7.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("1050.00")}]},
        )
        cols, rows, _, summary = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "group_mode": "period"})
        self.assertEqual(len(rows), 2)
        smap = {r["supplier"]: r for r in rows}
        self.assertEqual(smap[self.supplier1.name]["sold_qty"], Decimal("4.00"))
        self.assertEqual(smap[self.supplier1.name]["revenue"], "600.00")
        self.assertEqual(smap[self.supplier2.name]["sold_qty"], Decimal("3.00"))
        self.assertEqual(smap[self.supplier2.name]["revenue"], "450.00")

    # 4. FIFO supplier attribution
    def test_04_fifo_supplier_attribution(self):
        # Entry 1 (Supplier 1) created first
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("1000.00"),
        )
        # Entry 2 (Supplier 2) created second
        StockEntryService.create_entry(
            supplier=self.supplier2, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("110.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("1100.00"),
        )
        # Sale 6 units -> MUST strictly allocate from Supplier 1
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("6.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("900.00")}]},
        )
        cols, rows, _, summary = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["supplier"], self.supplier1.name)
        self.assertEqual(rows[0]["sold_qty"], Decimal("6.00"))

    # 5. Return to original supplier / lot
    def test_05_return_to_original_supplier_lot(self):
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("1000.00"),
        )
        sale = SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("5.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("750.00")}]},
        )
        item = sale.items.first()
        SaleReturnService.create_return(
            user=self.admin,
            data={
                "sale": sale.id,
                "comment": "Changed mind",
                "items": [{"sale_item": item.id, "quantity": Decimal("2.00")}],
            },
        )

        cols, rows, _, summary = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "group_mode": "period"})
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["supplier"], self.supplier1.name)
        self.assertEqual(row["sold_qty"], Decimal("5.00"))
        self.assertEqual(row["returned_qty"], Decimal("2.00"))
        self.assertEqual(row["net_sold_qty"], Decimal("3.00"))
        self.assertEqual(Decimal(row["revenue"]), Decimal("450.00"))

    # 6. Cross-period return
    def test_06_cross_period_return(self):
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("1000.00"),
        )
        # Past sale: July 15
        past_sale_dt = timezone.make_aware(datetime(2026, 7, 15, 12, 0, 0))
        sale = SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("5.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("750.00")}]},
        )
        # Set past timestamp on sale and allocations
        Sale.objects.filter(pk=sale.pk).update(created_at=past_sale_dt)
        StockAllocation.objects.filter(sale_item__sale=sale).update(created_at=past_sale_dt)

        # Return made on August 10
        ret = SaleReturnService.create_return(
            user=self.admin,
            data={
                "sale": sale.id,
                "comment": "Defect",
                "items": [{"sale_item": sale.items.first().id, "quantity": Decimal("2.00")}],
            },
        )
        aug_return_dt = timezone.make_aware(datetime(2026, 8, 10, 14, 0, 0))
        StockAllocation.objects.filter(movement_type=StockAllocation.MovementType.SALE_RETURN).update(created_at=aug_return_dt)

        # Report for August 2026: sale is excluded, return is captured and attributed to supplier1
        params = {"report_type": "supplier_sales", "from": "2026-08-01", "to": "2026-08-31", "store_id": self.store1.id, "group_mode": "period"}
        cols, rows, _, summary = SupplierSalesReportService.build_report(params)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["supplier"], self.supplier1.name)
        self.assertEqual(row["sold_qty"], Decimal("0.00"))
        self.assertEqual(row["returned_qty"], Decimal("2.00"))
        self.assertEqual(row["net_sold_qty"], Decimal("-2.00"))
        self.assertEqual(Decimal(row["revenue"]), Decimal("-300.00"))

    # 7. Partial return
    def test_07_partial_return(self):
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("1000.00"),
        )
        sale = SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("10.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("1500.00")}]},
        )
        SaleReturnService.create_return(
            user=self.admin,
            data={
                "sale": sale.id,
                "comment": "Partial",
                "items": [{"sale_item": sale.items.first().id, "quantity": Decimal("3.00")}],
            },
        )
        cols, rows, _, summary = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "group_mode": "period"})
        self.assertEqual(rows[0]["sold_qty"], Decimal("10.00"))
        self.assertEqual(rows[0]["returned_qty"], Decimal("3.00"))
        self.assertEqual(rows[0]["net_sold_qty"], Decimal("7.00"))
        self.assertEqual(Decimal(rows[0]["revenue"]), Decimal("1050.00"))

    # 8. Full return
    def test_08_full_return(self):
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("5.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("500.00"),
        )
        sale = SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("5.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("750.00")}]},
        )
        SaleReturnService.create_return(
            user=self.admin,
            data={
                "sale": sale.id,
                "comment": "Full Return",
                "items": [{"sale_item": sale.items.first().id, "quantity": Decimal("5.00")}],
            },
        )
        cols, rows, _, summary = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "group_mode": "period"})
        self.assertEqual(rows[0]["sold_qty"], Decimal("5.00"))
        self.assertEqual(rows[0]["returned_qty"], Decimal("5.00"))
        self.assertEqual(rows[0]["net_sold_qty"], Decimal("0.00"))
        self.assertEqual(Decimal(rows[0]["revenue"]), Decimal("0.00"))

    # 9. Supplier filter
    def test_09_supplier_filter(self):
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("5.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("500.00"))
        StockEntryService.create_entry(supplier=self.supplier2, store=self.store1, user=self.admin, items=[{"product": self.product2, "quantity": Decimal("5.00"), "purchase_price": Decimal("50.00"), "selling_price": Decimal("80.00"), "wholesale_price": Decimal("70.00")}], cash_amount=Decimal("250.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]})
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product2.id, "quantity": Decimal("3.00"), "price": Decimal("80.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("240.00")}]})

        cols, rows, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "supplier_id": str(self.supplier1.id)})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["supplier"], self.supplier1.name)

    # 10. Store isolation
    def test_10_store_isolation(self):
        # Store 1 sale
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("5.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("500.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]})

        # Store 2 sale
        StockEntryService.create_entry(supplier=self.supplier2, store=self.store2, user=self.admin, items=[{"product": self.product2, "quantity": Decimal("5.00"), "purchase_price": Decimal("50.00"), "selling_price": Decimal("80.00"), "wholesale_price": Decimal("70.00")}], cash_amount=Decimal("250.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store2.id, "customer": self.customer.id, "items": [{"product": self.product2.id, "quantity": Decimal("3.00"), "price": Decimal("80.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("240.00")}]})

        # When store1_mgr queries: Store 2 rows are NEVER visible
        cols, rows, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales"}, user=self.store1_mgr)
        self.assertTrue(all(r["store"] == self.store1.name for r in rows))
        self.assertFalse(any(r["store"] == self.store2.name for r in rows))

    # 11. Category, brand, product, SKU, barcode filters are omitted/ignored (Billz contract parity)
    def test_11_category_brand_product_sku_barcode_filters(self):
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("5.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("500.00"))
        StockEntryService.create_entry(supplier=self.supplier2, store=self.store1, user=self.admin, items=[{"product": self.product2, "quantity": Decimal("5.00"), "purchase_price": Decimal("50.00"), "selling_price": Decimal("80.00"), "wholesale_price": Decimal("70.00")}], cash_amount=Decimal("250.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("1.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("150.00")}]})
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product2.id, "quantity": Decimal("1.00"), "price": Decimal("80.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("80.00")}]})

        # Passing category_id does not filter away other categories
        _, rows_cat, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "category_id": str(self.cat_brakes.id)})
        self.assertEqual(len(rows_cat), 2)

        # Passing brand_id does not filter away other brands
        _, rows_brand, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "brand_id": str(self.brand_brembo.id)})
        self.assertEqual(len(rows_brand), 2)

        # Passing sku does not filter away other SKUs
        _, rows_sku, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "sku": "BP-001"})
        self.assertEqual(len(rows_sku), 2)

        # Passing barcode does not filter away other barcodes
        _, rows_bar, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "barcode": "4781001000025"})
        self.assertEqual(len(rows_bar), 2)

    # 12. Seller and Customer filters are omitted/ignored (Billz contract parity)
    def test_12_seller_customer_filters(self):
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        # Sale by seller_user
        SaleService.create_sale(user=self.seller_user, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]})
        # Sale by admin with no customer
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": None, "items": [{"product": self.product1.id, "quantity": Decimal("3.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("450.00")}]})

        # Filter by seller is omitted; returns total 5.00 sold
        _, rows_sel, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "seller_id": str(self.seller_user.id)})
        self.assertEqual(len(rows_sel), 1)
        self.assertEqual(rows_sel[0]["sold_qty"], Decimal("5.00"))

        # Filter by customer is omitted; returns total 5.00 sold
        _, rows_cust, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "customer_id": str(self.customer.id)})
        self.assertEqual(len(rows_cust), 1)
        self.assertEqual(rows_cust[0]["sold_qty"], Decimal("5.00"))

    # 13. Discount and revenue correctness
    def test_13_discount_and_revenue_correctness(self):
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        # 10 units @ 150 = 1500 subtotal, with 10% discount => total_amount = 1350
        SaleService.create_sale(
            user=self.admin,
            data={
                "store": self.store1.id, "customer": self.customer.id,
                "items": [{"product": self.product1.id, "quantity": Decimal("10.00"), "price": Decimal("150.00")}],
                "discount_type": "p", "discount_value": Decimal("10.00"),
                "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("1350.00")}],
            },
        )
        _, rows, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id})
        self.assertEqual(Decimal(rows[0]["revenue"]), Decimal("1350.00"))

    # 14. Free price condition
    def test_14_free_price(self):
        # Catalog selling_price is 150.00
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        # Sold at custom free price of 165.00
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("165.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("330.00")}]},
        )
        _, rows, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id})
        self.assertEqual(rows[0]["free_price"], "Ha")

    # 15. Wholesale price condition
    def test_15_wholesale_price(self):
        # Catalog wholesale_price is 130.00
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        # Sold at wholesale price 130.00
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("4.00"), "price": Decimal("130.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("520.00")}]},
        )
        _, rows, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id})
        self.assertEqual(rows[0]["used_wholesale_price"], "Ha")
        self.assertEqual(rows[0]["free_price"], "Yo'q")

    # 16. Summary equals filtered rows
    def test_16_summary_equals_filtered_rows(self):
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        StockEntryService.create_entry(supplier=self.supplier2, store=self.store1, user=self.admin, items=[{"product": self.product2, "quantity": Decimal("20.00"), "purchase_price": Decimal("50.00"), "selling_price": Decimal("80.00"), "wholesale_price": Decimal("70.00")}], cash_amount=Decimal("1000.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("3.00"), "price": Decimal("150.00")}, {"product": self.product2.id, "quantity": Decimal("5.00"), "price": Decimal("80.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("850.00")}]})

        cols, rows, _, summary = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "group_mode": "period"})
        sum_dict = {s["label"]: s["value"] for s in summary}
        self.assertEqual(sum_dict["Qatorlar"], len(rows))
        self.assertEqual(sum_dict["Jami sotilgan"], sum(r["sold_qty"] for r in rows))
        self.assertEqual(Decimal(str(sum_dict["Jami tushum"])), sum(Decimal(r["revenue"]) for r in rows))

    # 17. Excel filtered export
    def test_17_excel_filtered_export(self):
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]})

        request = self.factory.get("/api/reports/builder/export/", {"report_type": "supplier_sales", "export_type": "excel", "store_id": self.store1.id})
        force_authenticate(request, user=self.export_user)
        response = ReportBuilderExportAPIView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertTrue(len(response.content) > 100)

    # 18. CSV filtered export
    def test_18_csv_filtered_export(self):
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]})

        request = self.factory.get("/api/reports/builder/export/", {"report_type": "supplier_sales", "export_type": "csv", "store_id": self.store1.id})
        force_authenticate(request, user=self.export_user)
        response = ReportBuilderExportAPIView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        content = response.content.decode("utf-8-sig")
        self.assertIn("Yetkazib beruvchi", content)
        self.assertIn(self.supplier1.name, content)

    # 19. PDF filtered export
    def test_19_pdf_filtered_export(self):
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]})

        request = self.factory.get("/api/reports/builder/export/", {"report_type": "supplier_sales", "export_type": "pdf", "store_id": self.store1.id})
        force_authenticate(request, user=self.export_user)
        response = ReportBuilderExportAPIView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    # 20. RBAC view and export
    def test_20_rbac_view_and_export(self):
        # 1. User without view permission => 403 on generate
        req1 = self.factory.get("/api/reports/builder/", {"report_type": "supplier_sales"})
        force_authenticate(req1, user=self.no_perm_user)
        res1 = ReportBuilderGenerateAPIView.as_view()(req1)
        self.assertEqual(res1.status_code, 403)

        # 2. User with view permission but without export permission => 403 on export
        req2 = self.factory.get("/api/reports/builder/export/", {"report_type": "supplier_sales", "export_type": "excel"})
        force_authenticate(req2, user=self.store1_mgr)
        res2 = ReportBuilderExportAPIView.as_view()(req2)
        self.assertEqual(res2.status_code, 403)

        # 3. User with export permission => 200 OK
        req3 = self.factory.get("/api/reports/builder/export/", {"report_type": "supplier_sales", "export_type": "excel"})
        force_authenticate(req3, user=self.export_user)
        res3 = ReportBuilderExportAPIView.as_view()(req3)
        self.assertEqual(res3.status_code, 200)

    # 21. No heuristic supplier attribution
    def test_21_no_heuristic_supplier_attribution(self):
        # First entry from Supplier 1
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        # Sale consumes Supplier 1
        sale = SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("5.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("750.00")}]})
        # Later a brand new entry is made from Supplier 2
        StockEntryService.create_entry(supplier=self.supplier2, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("20.00"), "purchase_price": Decimal("105.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("2100.00"))

        cols, rows, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id})
        # Must strictly attribute the sale to Supplier 1, NEVER to the latest Supplier 2!
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["supplier"], self.supplier1.name)
        self.assertNotEqual(rows[0]["supplier"], self.supplier2.name)

    # 22. Historical unknown supplier handling
    def test_22_historical_unknown_supplier_handling(self):
        # Pre-create an OPENING_BALANCE lot with supplier=None (as created by cut-over migration)
        ProductBatch.objects.create(store=self.store1, product=self.product1, quantity=Decimal("10.00"), purchase_price=Decimal("100.00"), selling_price=Decimal("150.00"))
        StockLot.objects.create(
            store=self.store1, product=self.product1, lot_type=StockLot.LotType.OPENING_BALANCE,
            supplier=None, initial_quantity=Decimal("10.00"), remaining_quantity=Decimal("10.00"), purchase_price=Decimal("100.00"),
        )
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("4.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("600.00")}]})

        cols, rows, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["supplier"], "Tarixiy (Aniqlanmagan)")
        self.assertEqual(rows[0]["sold_qty"], Decimal("4.00"))

    # 23. Query count bounded (No N+1)
    def test_23_query_count_bounded(self):
        # Create entries and multiple sales
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("20.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("2000.00"))
        for _ in range(5):
            SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("1.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("150.00")}]})

        with self.assertNumQueries(4):
            cols, rows, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id})
            self.assertEqual(len(rows), 1)

    # 24. Multi-store same product
    def test_24_multi_store_same_product(self):
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store2, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))

        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("3.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("450.00")}]})
        SaleService.create_sale(user=self.admin, data={"store": self.store2.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("4.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("600.00")}]})

        cols, rows, _, summary = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "group_mode": "period"})
        # 2 distinct rows: one for Store Alpha, one for Store Beta
        self.assertEqual(len(rows), 2)
        store_map = {r["store"]: r for r in rows}
        self.assertEqual(store_map[self.store1.name]["sold_qty"], Decimal("3.00"))
        self.assertEqual(store_map[self.store2.name]["sold_qty"], Decimal("4.00"))

    # 25. Zero result dataset
    def test_25_zero_result_dataset(self):
        cols, rows, _, summary = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "supplier_id": "999999"})
        self.assertEqual(len(rows), 0)
        self.assertEqual(len(cols), 13)
        sum_dict = {s["label"]: s["value"] for s in summary}
        self.assertEqual(sum_dict["Qatorlar"], 0)
        self.assertEqual(sum_dict["Jami sotilgan"], Decimal("0.00"))

    # 26. Product optional: all products returned when product_id is None, empty, "all", or omitted
    def test_26_product_optional_all_products_when_omitted(self):
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("1000.00"),
        )
        StockEntryService.create_entry(
            supplier=self.supplier2, store=self.store1, user=self.admin,
            items=[{"product": self.product2, "quantity": Decimal("10.00"), "purchase_price": Decimal("50.00"), "selling_price": Decimal("80.00"), "wholesale_price": Decimal("70.00")}],
            cash_amount=Decimal("500.00"),
        )
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]},
        )
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product2.id, "quantity": Decimal("3.00"), "price": Decimal("80.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("240.00")}]},
        )

        # Test case A: omitted product_id
        _, rows_omitted, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "group_mode": "period"})
        self.assertEqual(len(rows_omitted), 2)
        prod_names = {r["product"] for r in rows_omitted}
        self.assertIn(self.product1.name, prod_names)
        self.assertIn(self.product2.name, prod_names)

        # Test case B: product_id = None
        _, rows_none, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "product_id": None, "group_mode": "period"})
        self.assertEqual(len(rows_none), 2)

        # Test case C: product_id = ""
        _, rows_empty, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "product_id": "", "group_mode": "period"})
        self.assertEqual(len(rows_empty), 2)

        # Test case D: product_id = "all"
        _, rows_all, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "product_id": "all", "group_mode": "period"})
        self.assertEqual(len(rows_all), 2)

    # 27. Product filter: omitted from contract, all products returned even if product_id is specified
    def test_27_product_filter_single_product_when_specified(self):
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("1000.00"),
        )
        StockEntryService.create_entry(
            supplier=self.supplier2, store=self.store1, user=self.admin,
            items=[{"product": self.product2, "quantity": Decimal("10.00"), "purchase_price": Decimal("50.00"), "selling_price": Decimal("80.00"), "wholesale_price": Decimal("70.00")}],
            cash_amount=Decimal("500.00"),
        )
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]},
        )
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product2.id, "quantity": Decimal("3.00"), "price": Decimal("80.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("240.00")}]},
        )

        # Passing product_id does not filter away product 2 (all products returned)
        _, rows_p1, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "product_id": str(self.product1.id)})
        self.assertEqual(len(rows_p1), 2)

        _, rows_p2, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "product_id": str(self.product2.id)})
        self.assertEqual(len(rows_p2), 2)

    # 28. Report builder metadata: supplier_sales has Billz toolbar filters and search=False
    def test_28_meta_reports_definition_no_product_filter(self):
        meta = ReportBuilderService.meta()
        sup_spec = next((r for r in meta["reports"] if r["key"] == "supplier_sales"), None)
        self.assertIsNotNone(sup_spec)
        self.assertFalse(sup_spec["search"])

        filter_params = [f.get("param") for f in sup_spec["filters"]]
        # Omitted filters
        self.assertNotIn("product_id", filter_params)
        self.assertNotIn("category_id", filter_params)
        self.assertNotIn("brand_id", filter_params)
        self.assertNotIn("seller_id", filter_params)

        # Billz toolbar filters present
        self.assertIn("supplier_id", filter_params)
        self.assertIn("group_mode", filter_params)
        self.assertIn("consolidate_stores", filter_params)
        self.assertIn("price_type", filter_params)
        self.assertIn("store_id", filter_params)

        # Other reports that genuinely need product (like product_history) still keep their product filter
        history_spec = next((r for r in meta["reports"] if r["key"] == "product_history"), None)
        self.assertIsNotNone(history_spec)
        history_prod_filter = next((f for f in history_spec["filters"] if f.get("param") == "product_id"), None)
        self.assertIsNotNone(history_prod_filter)
        self.assertEqual(history_prod_filter["type"], "product")

    # 29. API generate endpoint without product_id returns 200 OK with all products
    def test_29_api_generate_without_product_id(self):
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("1000.00"),
        )
        StockEntryService.create_entry(
            supplier=self.supplier2, store=self.store1, user=self.admin,
            items=[{"product": self.product2, "quantity": Decimal("10.00"), "purchase_price": Decimal("50.00"), "selling_price": Decimal("80.00"), "wholesale_price": Decimal("70.00")}],
            cash_amount=Decimal("500.00"),
        )
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("1.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("150.00")}]},
        )
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product2.id, "quantity": Decimal("2.00"), "price": Decimal("80.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("160.00")}]},
        )

        request = self.factory.get("/api/reports/builder/", {"report_type": "supplier_sales", "store_id": self.store1.id})
        force_authenticate(request, user=self.store1_mgr)
        response = ReportBuilderGenerateAPIView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["rows"]), 2)
        returned_products = {r["product"] for r in response.data["rows"]}
        self.assertIn(self.product1.name, returned_products)
        self.assertIn(self.product2.name, returned_products)

    # 30. Exports without product_id exports all products (Excel, CSV, PDF)
    def test_30_exports_without_product_id(self):
        StockEntryService.create_entry(
            supplier=self.supplier1, store=self.store1, user=self.admin,
            items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}],
            cash_amount=Decimal("1000.00"),
        )
        StockEntryService.create_entry(
            supplier=self.supplier2, store=self.store1, user=self.admin,
            items=[{"product": self.product2, "quantity": Decimal("10.00"), "purchase_price": Decimal("50.00"), "selling_price": Decimal("80.00"), "wholesale_price": Decimal("70.00")}],
            cash_amount=Decimal("500.00"),
        )
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]},
        )
        SaleService.create_sale(
            user=self.admin,
            data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product2.id, "quantity": Decimal("3.00"), "price": Decimal("80.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("240.00")}]},
        )

        # CSV export without product_id
        csv_req = self.factory.get("/api/reports/builder/export/", {"report_type": "supplier_sales", "export_type": "csv", "store_id": self.store1.id})
        force_authenticate(csv_req, user=self.export_user)
        csv_res = ReportBuilderExportAPIView.as_view()(csv_req)
        self.assertEqual(csv_res.status_code, 200)
        csv_content = csv_res.content.decode("utf-8-sig")
        self.assertIn(self.product1.name, csv_content)
        self.assertIn(self.product2.name, csv_content)

        # Excel export without product_id
        xlsx_req = self.factory.get("/api/reports/builder/export/", {"report_type": "supplier_sales", "export_type": "excel", "store_id": self.store1.id})
        force_authenticate(xlsx_req, user=self.export_user)
        xlsx_res = ReportBuilderExportAPIView.as_view()(xlsx_req)
        self.assertEqual(xlsx_res.status_code, 200)
        self.assertTrue(len(xlsx_res.content) > 100)

        # PDF export without product_id
        pdf_req = self.factory.get("/api/reports/builder/export/", {"report_type": "supplier_sales", "export_type": "pdf", "store_id": self.store1.id})
        force_authenticate(pdf_req, user=self.export_user)
        pdf_res = ReportBuilderExportAPIView.as_view()(pdf_req)
        self.assertEqual(pdf_res.status_code, 200)
        self.assertTrue(pdf_res.content.startswith(b"%PDF"))

    # 31. Store isolation via ReportBuilderService with needs_user
    def test_31_store_isolation_via_builder_needs_user(self):
        # Store 1 sale
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("5.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("500.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]})

        # Store 2 sale
        StockEntryService.create_entry(supplier=self.supplier2, store=self.store2, user=self.admin, items=[{"product": self.product2, "quantity": Decimal("5.00"), "purchase_price": Decimal("50.00"), "selling_price": Decimal("80.00"), "wholesale_price": Decimal("70.00")}], cash_amount=Decimal("250.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store2.id, "customer": self.customer.id, "items": [{"product": self.product2.id, "quantity": Decimal("3.00"), "price": Decimal("80.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("240.00")}]})

        # When store1_mgr generates via API without specifying store_id
        req = self.factory.get("/api/reports/builder/", {"report_type": "supplier_sales"})
        force_authenticate(req, user=self.store1_mgr)
        res = ReportBuilderGenerateAPIView.as_view()(req)
        self.assertEqual(res.status_code, 200)
        # Store 2 rows are completely excluded
        self.assertTrue(all(r["store"] == self.store1.name for r in res.data["rows"]))
        self.assertFalse(any(r["store"] == self.store2.name for r in res.data["rows"]))

    # 32. Store consolidation: consolidate_stores false vs true
    def test_32_consolidate_stores_false_vs_true(self):
        # Product1 sold in Store1
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]})

        # Product1 sold in Store2
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store2, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store2.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("3.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("450.00")}]})

        # When consolidate_stores is False: 2 rows
        _, rows_no_cons, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "consolidate_stores": "false", "group_mode": "period"})
        self.assertEqual(len(rows_no_cons), 2)
        store_names = {r["store"] for r in rows_no_cons}
        self.assertIn(self.store1.name, store_names)
        self.assertIn(self.store2.name, store_names)

        # When consolidate_stores is True: 1 aggregated row
        _, rows_cons, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "consolidate_stores": "true", "group_mode": "period"})
        self.assertEqual(len(rows_cons), 1)
        self.assertEqual(rows_cons[0]["store"], "Barcha do'konlar")
        self.assertEqual(rows_cons[0]["sold_qty"], Decimal("5.00"))
        self.assertEqual(Decimal(rows_cons[0]["revenue"]), Decimal("750.00"))

    # 33. Price type filtering: retail, wholesale, free, all
    def test_33_price_type_filtering(self):
        # Product1: selling_price=150.00, wholesale_price=130.00
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("30.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("3000.00"))

        # 1. Retail sale (price=150.00)
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("1.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("150.00")}]})
        # 2. Wholesale sale (price=130.00)
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("130.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("260.00")}]})
        # 3. Free price sale (price=175.00)
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("3.00"), "price": Decimal("175.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("525.00")}]})

        # price_type = "retail"
        _, rows_ret, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "price_type": "retail"})
        self.assertEqual(len(rows_ret), 1)
        self.assertEqual(rows_ret[0]["sold_qty"], Decimal("1.00"))
        self.assertEqual(rows_ret[0]["free_price"], "Yo'q")
        self.assertEqual(rows_ret[0]["used_wholesale_price"], "Yo'q")

        # price_type = "wholesale"
        _, rows_ws, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "price_type": "wholesale"})
        self.assertEqual(len(rows_ws), 1)
        self.assertEqual(rows_ws[0]["sold_qty"], Decimal("2.00"))
        self.assertEqual(rows_ws[0]["used_wholesale_price"], "Ha")
        self.assertEqual(rows_ws[0]["free_price"], "Yo'q")

        # price_type = "free"
        _, rows_fr, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "price_type": "free"})
        self.assertEqual(len(rows_fr), 1)
        self.assertEqual(rows_fr[0]["sold_qty"], Decimal("3.00"))
        self.assertEqual(rows_fr[0]["free_price"], "Ha")
        self.assertEqual(rows_fr[0]["used_wholesale_price"], "Yo'q")

        # price_type = "all"
        _, rows_all, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales", "store_id": self.store1.id, "price_type": "all"})
        self.assertEqual(len(rows_all), 1)
        self.assertEqual(rows_all[0]["sold_qty"], Decimal("6.00"))
        self.assertEqual(rows_all[0]["free_price"], "Ha")
        self.assertEqual(rows_all[0]["used_wholesale_price"], "Ha")

    # 34. Exact 13 columns Billz contract parity
    def test_34_exact_13_columns_billz_order_and_kinds(self):
        cols, _, _, _ = SupplierSalesReportService.build_report({"report_type": "supplier_sales"})
        self.assertEqual(len(cols), 13)
        expected_keys = [
            "store",
            "date",
            "supplier",
            "product",
            "sku",
            "barcode",
            "categories_path",
            "sold_qty",
            "returned_qty",
            "net_sold_qty",
            "revenue",
            "free_price",
            "used_wholesale_price",
        ]
        self.assertEqual([c["key"] for c in cols], expected_keys)
        # Check kinds of flags
        free_col = next(c for c in cols if c["key"] == "free_price")
        ws_col = next(c for c in cols if c["key"] == "used_wholesale_price")
        self.assertEqual(free_col["kind"], "text")
        self.assertEqual(ws_col["kind"], "text")

    # 35. Excel A1 table structure: header on row 1, data on row 2, no metadata banner, no bottom summary
    def test_35_excel_a1_structure_and_no_summary_block(self):
        import openpyxl
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]})

        request = self.factory.get("/api/reports/builder/export/", {"report_type": "supplier_sales", "export_type": "excel", "store_id": self.store1.id})
        force_authenticate(request, user=self.export_user)
        response = ReportBuilderExportAPIView.as_view()(request)
        self.assertEqual(response.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        ws = wb.active
        # Row 1 must be headers directly
        self.assertEqual(ws.cell(row=1, column=1).value, "Do'kon")
        self.assertEqual(ws.cell(row=1, column=2).value, "Sana")
        self.assertEqual(ws.cell(row=1, column=3).value, "Yetkazib beruvchi")
        self.assertEqual(ws.cell(row=1, column=12).value, "Erkin narx")
        self.assertEqual(ws.cell(row=1, column=13).value, "Ulgurji narx")

        # Row 2 must be the first data row
        self.assertEqual(ws.cell(row=2, column=1).value, self.store1.name)
        self.assertEqual(ws.cell(row=2, column=3).value, self.supplier1.name)
        self.assertEqual(ws.cell(row=2, column=4).value, self.product1.name)

        # Row 3 must NOT contain any summary block
        self.assertIsNone(ws.cell(row=3, column=1).value)

    # 36. CSV clean structure: header row first, data rows follow, no summary section
    def test_36_csv_clean_structure_no_trailing_summary(self):
        StockEntryService.create_entry(supplier=self.supplier1, store=self.store1, user=self.admin, items=[{"product": self.product1, "quantity": Decimal("10.00"), "purchase_price": Decimal("100.00"), "selling_price": Decimal("150.00"), "wholesale_price": Decimal("130.00")}], cash_amount=Decimal("1000.00"))
        SaleService.create_sale(user=self.admin, data={"store": self.store1.id, "customer": self.customer.id, "items": [{"product": self.product1.id, "quantity": Decimal("2.00"), "price": Decimal("150.00")}], "payment_type": "cash", "payments": [{"type": "cash", "amount": Decimal("300.00")}]})

        request = self.factory.get("/api/reports/builder/export/", {"report_type": "supplier_sales", "export_type": "csv", "store_id": self.store1.id})
        force_authenticate(request, user=self.export_user)
        response = ReportBuilderExportAPIView.as_view()(request)
        self.assertEqual(response.status_code, 200)

        lines = [ln.strip() for ln in response.content.decode("utf-8-sig").splitlines() if ln.strip()]
        # Line 0 is header
        self.assertTrue(lines[0].startswith("Do'kon,Sana,Yetkazib beruvchi"))
        # Line 1 is data row
        self.assertIn(self.supplier1.name, lines[1])
        # Exactly 2 lines (header + 1 row), no summary rows
        self.assertEqual(len(lines), 2)


