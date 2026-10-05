from datetime import date, datetime, time
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.contract.models import Supplier
from apps.inventory.models import StockAllocation, StockLot
from apps.products.models import Brand, Category, Product, ProductBatch
from apps.reports.services.supplier_sales_report_service import SupplierSalesReportService
from apps.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store, StoreUser
from apps.users.models.customers import Customer
from apps.users.models.role import Role
from apps.users.models.user import User


class SupplierSalesUnallocatedPeriodAccountingTests(TestCase):
    """
    Comprehensive regression test suite for SupplierSalesReportService
    Unallocated / Historical Sales Period Transactional Accounting Migration.

    Guarantees verified:
    1. Dual independent streams: gross SaleItem in sale period, SaleReturnItem in return period.
    2. SaleItem.returned_quantity is NOT used for accounting.
    3. Cross-period full and partial returns.
    4. Return-only period produces negative net sales and negative revenue.
    5. Sale.Status.RETURNED is NOT excluded from sale period.
    6. Soft-deleted sales and returns are strictly excluded.
    7. Multi-store isolation and consolidation.
    8. Supplier and date filtering compliance.
    9. Bounded O(1) query complexity (no N+1).
    10. Lot-allocated and unallocated streams coexist without double counting.
    11. Line-level proportional discount effective price preservation on return.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tz = timezone.get_current_timezone()

        # Stores
        cls.store1 = Store.objects.create(name="Store Alpha", address="Alpha Address", phone_number="+998901111111")
        cls.store2 = Store.objects.create(name="Store Beta", address="Beta Address", phone_number="+998902222222")

        # Roles and Users
        cls.role_viewer = Role.objects.create(name="Supplier Viewer", permissions=["reports.view", "reports.supplier_sales.view"])
        cls.admin = User.objects.create(phone_number="+998900000001", full_name="Super Admin", is_superuser=True, is_staff=True)
        cls.store1_mgr = User.objects.create(phone_number="+998900000003", full_name="Store 1 Mgr", role=cls.role_viewer, is_superuser=False)
        StoreUser.objects.create(user=cls.store1_mgr, store=cls.store1, is_active=True)
        StoreUser.objects.create(user=cls.admin, store=cls.store1, is_active=True)
        StoreUser.objects.create(user=cls.admin, store=cls.store2, is_active=True)

        # Catalog
        cls.cat_parts = Category.objects.create(name="Auto Parts")
        cls.brand = Brand.objects.create(name="Bosch")
        cls.product1 = Product.objects.create(
            name="Spark Plug",
            barcode="4780000000001",
            sku="SP-001",
            category=cls.cat_parts,
            brand=cls.brand,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.product2 = Product.objects.create(
            name="Brake Disc",
            barcode="4780000000002",
            sku="BD-002",
            category=cls.cat_parts,
            brand=cls.brand,
            status=Product.ProductStatus.ACTIVE,
        )

        cls.supplier1 = Supplier.objects.create(name="Supplier One")
        cls.customer = Customer.objects.create(full_name="Vali Aliyev", phone_number="+998901112233")

        # Batches for catalog comparison
        ProductBatch.objects.create(
            store=cls.store1,
            product=cls.product1,
            quantity=Decimal("100"),
            purchase_price=Decimal("60.00"),
            selling_price=Decimal("100.00"),
            wholesale_price=Decimal("90.00"),
        )
        ProductBatch.objects.create(
            store=cls.store1,
            product=cls.product2,
            quantity=Decimal("50"),
            purchase_price=Decimal("120.00"),
            selling_price=Decimal("200.00"),
            wholesale_price=Decimal("180.00"),
        )
        ProductBatch.objects.create(
            store=cls.store2,
            product=cls.product1,
            quantity=Decimal("100"),
            purchase_price=Decimal("60.00"),
            selling_price=Decimal("100.00"),
            wholesale_price=Decimal("90.00"),
        )

        cls.SEPT_DATE = date(2026, 9, 10)
        cls.SEPT_DT = timezone.make_aware(datetime(2026, 9, 10, 11, 0, 0), cls.tz)
        cls.OCT_DATE = date(2026, 10, 15)
        cls.OCT_DT = timezone.make_aware(datetime(2026, 10, 15, 14, 0, 0), cls.tz)

    def _create_unalloc_sale(self, store, product, qty, unit_price, dt, status=Sale.Status.PAID, discount_amount=Decimal("0.00")):
        """Creates an unallocated historical sale (no stock_allocations)."""
        total = (qty * unit_price) - discount_amount
        sale = Sale.objects.create(
            store=store,
            seller=self.admin,
            customer=self.customer,
            total_amount=total,
            paid_amount=total,
            discount_amount=discount_amount,
            status=status,
            payment_type=Sale.PaymentType.CASH,
        )
        Sale.objects.filter(pk=sale.pk).update(created_at=dt)
        item = SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=qty,
            unit_price=unit_price,
            purchase_price=Decimal("60.00"),
            total_price=qty * unit_price,
            returned_quantity=Decimal("0.00"),
        )
        return sale, item

    def _create_unalloc_return(self, sale, sale_item, qty, dt, refund_total=None):
        """Creates an unallocated historical return (no stock_allocations)."""
        if refund_total is None:
            refund_total = qty * sale_item.unit_price
        ret = SaleReturn.objects.create(
            sale=sale,
            store=sale.store,
            seller=self.admin,
            customer=self.customer,
            total_refund=refund_total,
        )
        SaleReturn.objects.filter(pk=ret.pk).update(created_at=dt)
        ret_item = SaleReturnItem.objects.create(
            sale_return=ret,
            sale_item=sale_item,
            product=sale_item.product,
            quantity=qty,
            unit_price=sale_item.unit_price,
            total_price=refund_total,
        )
        return ret, ret_item

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Same-period partial and full return
    # ─────────────────────────────────────────────────────────────────────────
    def test_01_unallocated_same_period_partial_return(self):
        """Same period: Gross sold 10, returned 3 -> Net sold 7, revenue 700."""
        sale, item = self._create_unalloc_sale(
            store=self.store1, product=self.product1, qty=Decimal("10.00"),
            unit_price=Decimal("100.00"), dt=self.SEPT_DT,
        )
        # Return on same day or same period
        self._create_unalloc_return(sale, item, qty=Decimal("3.00"), dt=self.SEPT_DT)

        cols, rows, _, summary = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01",
            "to": "2026-09-30",
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["supplier"], "Tarixiy (Aniqlanmagan)")
        self.assertEqual(r["product"], self.product1.name)
        self.assertEqual(r["sold_qty"], Decimal("10.00"))
        self.assertEqual(r["returned_qty"], Decimal("3.00"))
        self.assertEqual(r["net_sold_qty"], Decimal("7.00"))
        self.assertEqual(Decimal(r["revenue"]), Decimal("700.00"))

        sum_map = {s["label"]: s["value"] for s in summary}
        self.assertEqual(sum_map["Jami sotilgan"], Decimal("10.00"))
        self.assertEqual(sum_map["Jami qaytarilgan"], Decimal("3.00"))
        self.assertEqual(sum_map["Jami sotilgan (sof)"], Decimal("7.00"))
        self.assertEqual(sum_map["Jami tushum"], "700.00")

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Cross-period full return
    # ─────────────────────────────────────────────────────────────────────────
    def test_02_unallocated_cross_period_full_return(self):
        """Sale in Sept (5 units), Full return in Oct (5 units)."""
        sale, item = self._create_unalloc_sale(
            store=self.store1, product=self.product1, qty=Decimal("5.00"),
            unit_price=Decimal("100.00"), dt=self.SEPT_DT,
        )
        # Even if legacy returned_quantity was set, it must not corrupt accounting
        SaleItem.objects.filter(pk=item.pk).update(returned_quantity=Decimal("5.00"))
        self._create_unalloc_return(sale, item, qty=Decimal("5.00"), dt=self.OCT_DT)

        # 1. September report: Gross sale preserved, return is 0
        _, sept_rows, _, sept_sum = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01",
            "to": "2026-09-30",
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(len(sept_rows), 1)
        self.assertEqual(sept_rows[0]["sold_qty"], Decimal("5.00"))
        self.assertEqual(sept_rows[0]["returned_qty"], Decimal("0.00"))
        self.assertEqual(sept_rows[0]["net_sold_qty"], Decimal("5.00"))
        self.assertEqual(Decimal(sept_rows[0]["revenue"]), Decimal("500.00"))

        # 2. October report: Sale is 0, Return is 5, net is -5, revenue is -500
        _, oct_rows, _, oct_sum = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-10-01",
            "to": "2026-10-31",
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(len(oct_rows), 1)
        self.assertEqual(oct_rows[0]["sold_qty"], Decimal("0.00"))
        self.assertEqual(oct_rows[0]["returned_qty"], Decimal("5.00"))
        self.assertEqual(oct_rows[0]["net_sold_qty"], Decimal("-5.00"))
        self.assertEqual(Decimal(oct_rows[0]["revenue"]), Decimal("-500.00"))

        # 3. Two-month combined report: Net is 0, revenue is 0
        _, all_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01",
            "to": "2026-10-31",
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(len(all_rows), 1)
        self.assertEqual(all_rows[0]["sold_qty"], Decimal("5.00"))
        self.assertEqual(all_rows[0]["returned_qty"], Decimal("5.00"))
        self.assertEqual(all_rows[0]["net_sold_qty"], Decimal("0.00"))
        self.assertEqual(Decimal(all_rows[0]["revenue"]), Decimal("0.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Cross-period partial return
    # ─────────────────────────────────────────────────────────────────────────
    def test_03_unallocated_cross_period_partial_return(self):
        """Sale in Sept (10 units @ 200 = 2000), Partial return in Oct (4 units @ 200 = 800)."""
        sale, item = self._create_unalloc_sale(
            store=self.store1, product=self.product2, qty=Decimal("10.00"),
            unit_price=Decimal("200.00"), dt=self.SEPT_DT,
        )
        self._create_unalloc_return(sale, item, qty=Decimal("4.00"), dt=self.OCT_DT)

        # September
        _, sept_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01",
            "to": "2026-09-30",
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(sept_rows[0]["sold_qty"], Decimal("10.00"))
        self.assertEqual(sept_rows[0]["returned_qty"], Decimal("0.00"))
        self.assertEqual(sept_rows[0]["net_sold_qty"], Decimal("10.00"))
        self.assertEqual(Decimal(sept_rows[0]["revenue"]), Decimal("2000.00"))

        # October
        _, oct_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-10-01",
            "to": "2026-10-31",
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(oct_rows[0]["sold_qty"], Decimal("0.00"))
        self.assertEqual(oct_rows[0]["returned_qty"], Decimal("4.00"))
        self.assertEqual(oct_rows[0]["net_sold_qty"], Decimal("-4.00"))
        self.assertEqual(Decimal(oct_rows[0]["revenue"]), Decimal("-800.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Return-only period
    # ─────────────────────────────────────────────────────────────────────────
    def test_04_unallocated_return_only_period(self):
        """Historical sale in July, return in October. October has 0 sales."""
        july_dt = timezone.make_aware(datetime(2026, 7, 10, 10, 0, 0), self.tz)
        sale, item = self._create_unalloc_sale(
            store=self.store1, product=self.product1, qty=Decimal("2.00"),
            unit_price=Decimal("100.00"), dt=july_dt,
        )
        self._create_unalloc_return(sale, item, qty=Decimal("2.00"), dt=self.OCT_DT)

        _, rows, _, summary = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-10-01",
            "to": "2026-10-31",
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["sold_qty"], Decimal("0.00"))
        self.assertEqual(r["returned_qty"], Decimal("2.00"))
        self.assertEqual(r["net_sold_qty"], Decimal("-2.00"))
        self.assertEqual(Decimal(r["revenue"]), Decimal("-200.00"))

        sum_map = {s["label"]: s["value"] for s in summary}
        self.assertEqual(sum_map["Jami sotilgan"], Decimal("0.00"))
        self.assertEqual(sum_map["Jami qaytarilgan"], Decimal("2.00"))
        self.assertEqual(sum_map["Jami sotilgan (sof)"], Decimal("-2.00"))
        self.assertEqual(sum_map["Jami tushum"], "-200.00")

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Sale.Status.RETURNED included in sale period
    # ─────────────────────────────────────────────────────────────────────────
    def test_05_unallocated_sale_status_returned_included(self):
        """Sale marked status=RETURNED must still be included in sale period."""
        sale, item = self._create_unalloc_sale(
            store=self.store1, product=self.product1, qty=Decimal("4.00"),
            unit_price=Decimal("100.00"), dt=self.SEPT_DT,
            status=Sale.Status.RETURNED,
        )
        # Return happens in October
        self._create_unalloc_return(sale, item, qty=Decimal("4.00"), dt=self.OCT_DT)

        # In September, sale MUST NOT be excluded
        _, sept_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01",
            "to": "2026-09-30",
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(len(sept_rows), 1)
        self.assertEqual(sept_rows[0]["sold_qty"], Decimal("4.00"))
        self.assertEqual(sept_rows[0]["returned_qty"], Decimal("0.00"))
        self.assertEqual(sept_rows[0]["net_sold_qty"], Decimal("4.00"))
        self.assertEqual(Decimal(sept_rows[0]["revenue"]), Decimal("400.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Soft-deleted sales and returns excluded
    # ─────────────────────────────────────────────────────────────────────────
    def test_06_unallocated_soft_deleted_excluded(self):
        """Soft-deleted sales and returns must be excluded from all periods."""
        sale, item = self._create_unalloc_sale(
            store=self.store1, product=self.product1, qty=Decimal("6.00"),
            unit_price=Decimal("100.00"), dt=self.SEPT_DT,
        )
        self._create_unalloc_return(sale, item, qty=Decimal("6.00"), dt=self.OCT_DT)

        # Soft delete the sale
        Sale.objects.filter(pk=sale.pk).update(deleted_at=timezone.now())

        # September report must be empty
        _, sept_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01",
            "to": "2026-09-30",
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(len(sept_rows), 0)

        # October report must also exclude the return of soft-deleted sale
        _, oct_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-10-01",
            "to": "2026-10-31",
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(len(oct_rows), 0)

    # ─────────────────────────────────────────────────────────────────────────
    # 7. Multi-store isolation and consolidation
    # ─────────────────────────────────────────────────────────────────────────
    def test_07_unallocated_multi_store_isolation(self):
        """Store 1 and Store 2 unallocated data must be strictly isolated."""
        # Store 1: 5 units of product1
        sale1, it1 = self._create_unalloc_sale(self.store1, self.product1, Decimal("5.00"), Decimal("100.00"), self.SEPT_DT)
        # Store 2: 8 units of product1
        sale2, it2 = self._create_unalloc_sale(self.store2, self.product1, Decimal("8.00"), Decimal("100.00"), self.SEPT_DT)

        # Store 1 filter
        _, s1_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01", "to": "2026-09-30",
            "store_id": self.store1.id, "group_mode": "period",
        })
        self.assertEqual(len(s1_rows), 1)
        self.assertEqual(s1_rows[0]["store"], self.store1.name)
        self.assertEqual(s1_rows[0]["sold_qty"], Decimal("5.00"))

        # Store 2 filter
        _, s2_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01", "to": "2026-09-30",
            "store_id": self.store2.id, "group_mode": "period",
        })
        self.assertEqual(len(s2_rows), 1)
        self.assertEqual(s2_rows[0]["store"], self.store2.name)
        self.assertEqual(s2_rows[0]["sold_qty"], Decimal("8.00"))

        # RBAC user for Store 1
        _, rbac_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01", "to": "2026-09-30",
            "group_mode": "period",
        }, user=self.store1_mgr)
        self.assertEqual(len(rbac_rows), 1)
        self.assertEqual(rbac_rows[0]["store"], self.store1.name)

        # Consolidate stores = true
        _, cons_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01", "to": "2026-09-30",
            "consolidate_stores": "true", "group_mode": "period",
        })
        self.assertEqual(len(cons_rows), 1)
        self.assertEqual(cons_rows[0]["store"], "Barcha do'konlar")
        self.assertEqual(cons_rows[0]["sold_qty"], Decimal("13.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 8. Date and Supplier filtering
    # ─────────────────────────────────────────────────────────────────────────
    def test_08_unallocated_supplier_filtering(self):
        """When specific supplier ID is filtered, unallocated sales must be excluded."""
        self._create_unalloc_sale(self.store1, self.product1, Decimal("5.00"), Decimal("100.00"), self.SEPT_DT)

        # Filter for supplier 1: unallocated must be excluded
        _, sup1_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01", "to": "2026-09-30",
            "supplier_id": str(self.supplier1.id),
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(len(sup1_rows), 0)

        # Filter for "unknown" / "legacy": unallocated must be included
        _, legacy_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01", "to": "2026-09-30",
            "supplier_id": "unknown",
            "store_id": self.store1.id,
            "group_mode": "period",
        })
        self.assertEqual(len(legacy_rows), 1)
        self.assertEqual(legacy_rows[0]["supplier"], "Tarixiy (Aniqlanmagan)")

    # ─────────────────────────────────────────────────────────────────────────
    # 9. Query count performance bounded (O(1))
    # ─────────────────────────────────────────────────────────────────────────
    def test_09_unallocated_query_count_bounded(self):
        """Verify that scaling the number of unallocated sales and returns has constant query count."""
        for i in range(10):
            sale, it = self._create_unalloc_sale(
                self.store1, self.product1, Decimal("1.00"), Decimal("100.00"), self.SEPT_DT
            )
            if i % 2 == 0:
                self._create_unalloc_return(sale, it, Decimal("1.00"), self.OCT_DT)

        # Exactly 5 bounded queries: sales_alloc, return_alloc, unalloc_sales, unalloc_returns, batch_prices
        with self.assertNumQueries(5):
            cols, rows, _, summary = SupplierSalesReportService.build_report({
                "report_type": "supplier_sales",
                "from": "2026-09-01",
                "to": "2026-10-31",
                "store_id": self.store1.id,
                "group_mode": "period",
            })
            self.assertEqual(len(rows), 1)

    # ─────────────────────────────────────────────────────────────────────────
    # 10. Lot-allocated and unallocated coexist with no double counting
    # ─────────────────────────────────────────────────────────────────────────
    def test_10_lot_allocated_and_unallocated_coexist(self):
        """Allocated returns must only hit the allocated branch, unallocated only hit unallocated branch."""
        # 1. Lot-allocated sale & return
        lot = StockLot.objects.create(
            store=self.store1, product=self.product1, supplier=self.supplier1,
            lot_type=StockLot.LotType.PURCHASE, initial_quantity=Decimal("20.00"),
            remaining_quantity=Decimal("15.00"), purchase_price=Decimal("60.00"),
        )
        sale_alloc = Sale.objects.create(
            store=self.store1, seller=self.admin, customer=self.customer,
            total_amount=Decimal("500.00"), paid_amount=Decimal("500.00"),
            status=Sale.Status.PAID, payment_type=Sale.PaymentType.CASH,
        )
        Sale.objects.filter(pk=sale_alloc.pk).update(created_at=self.SEPT_DT)
        si_alloc = SaleItem.objects.create(
            sale=sale_alloc, product=self.product1, quantity=Decimal("5.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("500.00"),
        )
        alloc_out = StockAllocation.objects.create(
            lot=lot, movement_type=StockAllocation.MovementType.SALE,
            direction=StockAllocation.Direction.OUT, quantity=Decimal("5.00"),
            unit_cost=Decimal("60.00"), sale_item=si_alloc,
        )
        StockAllocation.objects.filter(pk=alloc_out.pk).update(created_at=self.SEPT_DT)

        # 2. Unallocated historical sale & return
        sale_unalloc, si_unalloc = self._create_unalloc_sale(
            self.store1, self.product1, Decimal("3.00"), Decimal("100.00"), self.SEPT_DT
        )
        self._create_unalloc_return(sale_unalloc, si_unalloc, Decimal("1.00"), self.SEPT_DT)

        cols, rows, _, summary = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01", "to": "2026-09-30",
            "store_id": self.store1.id, "group_mode": "period",
        })
        self.assertEqual(len(rows), 2)
        smap = {r["supplier"]: r for r in rows}
        self.assertIn(self.supplier1.name, smap)
        self.assertIn("Tarixiy (Aniqlanmagan)", smap)

        # Allocated
        self.assertEqual(smap[self.supplier1.name]["sold_qty"], Decimal("5.00"))
        self.assertEqual(smap[self.supplier1.name]["returned_qty"], Decimal("0.00"))

        # Unallocated
        self.assertEqual(smap["Tarixiy (Aniqlanmagan)"]["sold_qty"], Decimal("3.00"))
        self.assertEqual(smap["Tarixiy (Aniqlanmagan)"]["returned_qty"], Decimal("1.00"))
        self.assertEqual(smap["Tarixiy (Aniqlanmagan)"]["net_sold_qty"], Decimal("2.00"))
        self.assertEqual(Decimal(smap["Tarixiy (Aniqlanmagan)"]["revenue"]), Decimal("200.00"))

    # ─────────────────────────────────────────────────────────────────────────
    # 11. Proportional discount effective price preservation
    # ─────────────────────────────────────────────────────────────────────────
    def test_11_unallocated_discount_effective_price(self):
        """Sale: 10 units @ 100 = 1000, discount 200 -> total 800 (eff_price=80).
        Return in Oct: 2 units -> refund revenue must be 2 * 80 = 160.00."""
        sale, item = self._create_unalloc_sale(
            store=self.store1, product=self.product1, qty=Decimal("10.00"),
            unit_price=Decimal("100.00"), dt=self.SEPT_DT,
            discount_amount=Decimal("200.00"),
        )
        self._create_unalloc_return(sale, item, qty=Decimal("2.00"), dt=self.OCT_DT, refund_total=Decimal("160.00"))

        # September: Gross sale revenue = 10 * 80 = 800.00
        _, sept_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-09-01", "to": "2026-09-30",
            "store_id": self.store1.id, "group_mode": "period",
        })
        self.assertEqual(Decimal(sept_rows[0]["revenue"]), Decimal("800.00"))

        # October: Return revenue = -160.00
        _, oct_rows, _, _ = SupplierSalesReportService.build_report({
            "report_type": "supplier_sales",
            "from": "2026-10-01", "to": "2026-10-31",
            "store_id": self.store1.id, "group_mode": "period",
        })
        self.assertEqual(Decimal(oct_rows[0]["revenue"]), Decimal("-160.00"))
