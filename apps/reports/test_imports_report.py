from datetime import date, datetime, timedelta
from decimal import Decimal
import io
import json

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.contract.models import (
    StockEntry,
    StockEntryItem,
    StockEntryReturn,
    StockEntryReturnItem,
    Supplier,
)
from apps.products.models import Brand, Category, Product, ProductUnitMeasurement
from apps.reports.services.report_builder import ReportBuilderService
from apps.reports.views.report_builder_view import (
    ReportBuilderExportAPIView,
    ReportBuilderGenerateAPIView,
)
from apps.store.models import Store, StoreUser
from apps.users.models.role import Role
from apps.users.models.user import User


class ImportsReportTests(TestCase):
    """
    Kirimlar (Importlar) hisoboti test suite:
    - Sana oralig'isiz kirimlar (default_all=True)
    - Sana oralig'i bilan kirimlar
    - Store bo'yicha filtr
    - Supplier bo'yicha filtr
    - To'lov holati bo'yicha filtr
    - Qaytimli kirimlar filtri
    - Pagination va sorting
    - Text filterlar (entry_id, sku, barcode, search)
    - Info va Summary contract
    """

    @classmethod
    def setUpTestData(cls):
        # Stores
        cls.store1 = Store.objects.create(name="Store Alpha", address="Alpha Address", phone_number="+998901111111")
        cls.store2 = Store.objects.create(name="Store Beta", address="Beta Address", phone_number="+998902222222")

        # Roles
        cls.role_admin = Role.objects.create(name="Admin", permissions=["*"])
        cls.role_viewer = Role.objects.create(name="Imports Viewer", permissions=["reports.view", "reports.imports.view"])

        # Users
        cls.super_admin = User.objects.create_superuser(
            phone_number="+998990000001",
            full_name="Super Admin",
            role=cls.role_admin,
        )
        cls.manager = User.objects.create_user(
            phone_number="+998990000002",
            full_name="Store Manager",
            role=cls.role_viewer,
        )
        StoreUser.objects.create(user=cls.manager, store=cls.store1)

        # Category & Brand & Unit
        cls.category = Category.objects.create(name="Zapchastlar")
        cls.brand = Brand.objects.create(name="Bosch")
        cls.unit = ProductUnitMeasurement.objects.create(measurement="dona")

        # Products
        cls.product1 = Product.objects.create(
            name="Tormoz kolodkasi",
            sku="BRK-001",
            barcode="4780001110001",
            category=cls.category,
            brand=cls.brand,
            unit_measurement=cls.unit,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.product2 = Product.objects.create(
            name="Moy filtri",
            sku="FLT-002",
            barcode="4780001110002",
            category=cls.category,
            brand=cls.brand,
            unit_measurement=cls.unit,
            status=Product.ProductStatus.ACTIVE,
        )

        # Suppliers
        cls.supplier1 = Supplier.objects.create(
            name="Auto Parts OOO",
            phone_number="+998901234567",
            description="Asosiy yetkazib beruvchi",
        )
        cls.supplier2 = Supplier.objects.create(
            name="Global Import MCHJ",
            phone_number="+998907654321",
            description="Yordamchi yetkazib beruvchi",
        )

        # Stock Entries
        # Entry 1: Store 1, Supplier 1 (unpaid)
        cls.entry1 = StockEntry.objects.create(
            supplier=cls.supplier1,
            store=cls.store1,
            created_by=cls.super_admin,
            total_amount=Decimal("1000000.00"),
            cash_amount=Decimal("0.00"),
            card_amount=Decimal("0.00"),
            note="Kirim 1 izohi",
        )
        cls.item1 = StockEntryItem.objects.create(
            entry=cls.entry1,
            product=cls.product1,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("150000.00"),
            wholesale_price=Decimal("130000.00"),
        )

        # Entry 2: Store 2, Supplier 2 (full paid)
        cls.entry2 = StockEntry.objects.create(
            supplier=cls.supplier2,
            store=cls.store2,
            created_by=cls.super_admin,
            total_amount=Decimal("500000.00"),
            cash_amount=Decimal("500000.00"),
            card_amount=Decimal("0.00"),
            note="Kirim 2 izohi",
        )
        cls.item2 = StockEntryItem.objects.create(
            entry=cls.entry2,
            product=cls.product2,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("120000.00"),
            wholesale_price=Decimal("110000.00"),
        )

        # Entry 3: Store 1, Supplier 1 (has return)
        cls.entry3 = StockEntry.objects.create(
            supplier=cls.supplier1,
            store=cls.store1,
            created_by=cls.super_admin,
            total_amount=Decimal("400000.00"),
            cash_amount=Decimal("0.00"),
            card_amount=Decimal("0.00"),
            note="Kirim 3 qaytimli",
        )
        cls.item3 = StockEntryItem.objects.create(
            entry=cls.entry3,
            product=cls.product1,
            quantity=Decimal("4.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("140000.00"),
            wholesale_price=Decimal("120000.00"),
        )
        # Create return on item 3
        cls.ret3 = StockEntryReturn.objects.create(
            entry=cls.entry3,
            total_amount=Decimal("100000.00"),
            debt_cancelled=Decimal("100000.00"),
            refund_amount=Decimal("0.00"),
            created_by=cls.super_admin,
            note="Qaytim izohi",
        )
        cls.ret_item3 = StockEntryReturnItem.objects.create(
            stock_return=cls.ret3,
            entry_item=cls.item3,
            product=cls.product1,
            quantity=Decimal("1.00"),
            purchase_price=Decimal("100000.00"),
            amount=Decimal("100000.00"),
        )

    def setUp(self):
        self.factory = APIRequestFactory()
        self.generate_view = ReportBuilderGenerateAPIView.as_view()
        self.export_view = ReportBuilderExportAPIView.as_view()

    def test_imports_report_no_dates_default_all(self):
        """Sana berilmaganda barcha kirimlar chiqishi kerak (default_all=True)."""
        request = self.factory.get("/api/reports/builder/?report_type=imports&page=1&limit=25")
        force_authenticate(request, user=self.super_admin)
        response = self.generate_view(request)

        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data["total"], 3)
        self.assertEqual(len(data["rows"]), 3)
        self.assertEqual(len(data["summary"]), 9)
        self.assertIn("info", data)
        self.assertIn("title", data["info"])

    def test_imports_report_with_date_range(self):
        """Sana oralig'i to'g'ri ishlashi tekshiriladi."""
        today = timezone.localdate().isoformat()
        # Today covers entries created today
        request = self.factory.get(f"/api/reports/builder/?report_type=imports&from={today}&to={today}&page=1&limit=25")
        force_authenticate(request, user=self.super_admin)
        response = self.generate_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 3)

        # Future date range returns 0
        future = (timezone.localdate() + timedelta(days=10)).isoformat()
        future_end = (timezone.localdate() + timedelta(days=20)).isoformat()
        req_future = self.factory.get(f"/api/reports/builder/?report_type=imports&from={future}&to={future_end}")
        force_authenticate(req_future, user=self.super_admin)
        res_future = self.generate_view(req_future)
        self.assertEqual(res_future.status_code, 200)
        self.assertEqual(res_future.data["total"], 0)
        self.assertEqual(len(res_future.data["rows"]), 0)

    def test_imports_report_store_filter(self):
        """Store bo'yicha filtrlash to'g'ri ishlashi tekshiriladi."""
        request = self.factory.get(f"/api/reports/builder/?report_type=imports&store_id={self.store1.id}")
        force_authenticate(request, user=self.super_admin)
        response = self.generate_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 2)
        for row in response.data["rows"]:
            self.assertEqual(row["store_id"], self.store1.id)

    def test_imports_report_supplier_filter(self):
        """Yetkazib beruvchi bo'yicha filtrlash tekshiriladi."""
        request = self.factory.get(f"/api/reports/builder/?report_type=imports&supplier_id={self.supplier2.id}")
        force_authenticate(request, user=self.super_admin)
        response = self.generate_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["rows"][0]["supplier_id"], self.supplier2.id)

    def test_imports_report_payment_status_filter(self):
        """To'lov holati bo'yicha filtrlash tekshiriladi."""
        # Unpaid
        req_unpaid = self.factory.get("/api/reports/builder/?report_type=imports&payment_status=unpaid")
        force_authenticate(req_unpaid, user=self.super_admin)
        res_unpaid = self.generate_view(req_unpaid)
        self.assertEqual(res_unpaid.status_code, 200)
        self.assertEqual(res_unpaid.data["total"], 2)

        # Paid
        req_paid = self.factory.get("/api/reports/builder/?report_type=imports&payment_status=paid")
        force_authenticate(req_paid, user=self.super_admin)
        res_paid = self.generate_view(req_paid)
        self.assertEqual(res_paid.status_code, 200)
        self.assertEqual(res_paid.data["total"], 1)

    def test_imports_report_has_returns_filter(self):
        """Qaytimli kirimlar filtri tekshiriladi."""
        req_ret = self.factory.get("/api/reports/builder/?report_type=imports&has_returns=true")
        force_authenticate(req_ret, user=self.super_admin)
        res_ret = self.generate_view(req_ret)
        self.assertEqual(res_ret.status_code, 200)
        self.assertEqual(res_ret.data["total"], 1)
        self.assertEqual(res_ret.data["rows"][0]["entry_id"], self.entry3.id)

        req_no_ret = self.factory.get("/api/reports/builder/?report_type=imports&has_returns=false")
        force_authenticate(req_no_ret, user=self.super_admin)
        res_no_ret = self.generate_view(req_no_ret)
        self.assertEqual(res_no_ret.status_code, 200)
        self.assertEqual(res_no_ret.data["total"], 2)

    def test_imports_report_text_filters(self):
        """entry_id, sku, barcode, search text filtrlari tekshiriladi."""
        # entry_id
        req_entry = self.factory.get(f"/api/reports/builder/?report_type=imports&entry_id={self.entry1.id}")
        force_authenticate(req_entry, user=self.super_admin)
        res_entry = self.generate_view(req_entry)
        self.assertEqual(res_entry.data["total"], 1)
        self.assertEqual(res_entry.data["rows"][0]["entry_id"], self.entry1.id)

        # sku
        req_sku = self.factory.get("/api/reports/builder/?report_type=imports&sku=FLT-002")
        force_authenticate(req_sku, user=self.super_admin)
        res_sku = self.generate_view(req_sku)
        self.assertEqual(res_sku.data["total"], 1)
        self.assertEqual(res_sku.data["rows"][0]["sku"], "FLT-002")

        # barcode
        req_bc = self.factory.get("/api/reports/builder/?report_type=imports&barcode=4780001110001")
        force_authenticate(req_bc, user=self.super_admin)
        res_bc = self.generate_view(req_bc)
        self.assertEqual(res_bc.data["total"], 2)

        # search
        req_search = self.factory.get("/api/reports/builder/?report_type=imports&search=kolodkasi")
        force_authenticate(req_search, user=self.super_admin)
        res_search = self.generate_view(req_search)
        self.assertEqual(res_search.data["total"], 2)

    def test_imports_report_pagination(self):
        """Pagination (page, limit) to'g'ri ishlashi tekshiriladi."""
        req_page1 = self.factory.get("/api/reports/builder/?report_type=imports&page=1&limit=2")
        force_authenticate(req_page1, user=self.super_admin)
        res_page1 = self.generate_view(req_page1)
        self.assertEqual(res_page1.data["total"], 3)
        self.assertEqual(len(res_page1.data["rows"]), 2)
        self.assertEqual(res_page1.data["page"], 1)
        self.assertEqual(res_page1.data["limit"], 2)

        req_page2 = self.factory.get("/api/reports/builder/?report_type=imports&page=2&limit=2")
        force_authenticate(req_page2, user=self.super_admin)
        res_page2 = self.generate_view(req_page2)
        self.assertEqual(res_page2.data["total"], 3)
        self.assertEqual(len(res_page2.data["rows"]), 1)
        self.assertEqual(res_page2.data["page"], 2)

    def test_imports_report_manager_store_scoping(self):
        """Do'kon menejeri faqat o'z do'koni kirimlarini ko'rishi kerak."""
        request = self.factory.get("/api/reports/builder/?report_type=imports")
        force_authenticate(request, user=self.manager)
        response = self.generate_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 2)
        for row in response.data["rows"]:
            self.assertEqual(row["store_id"], self.store1.id)
