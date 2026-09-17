"""
Test suite for Excel worksheet name sanitization and Excel export across reports.
"""

import io
from datetime import date, datetime, timedelta
from decimal import Decimal
import openpyxl
import xlsxwriter
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.common.excel_export import safe_add_worksheet, sanitize_worksheet_name
from apps.products.models import Brand, Category, Product, ProductUnitMeasurement
from apps.reports.views.report_builder_view import ReportBuilderExportAPIView
from apps.store.models import Store, StoreUser
from apps.users.models.role import Role
from apps.users.models.user import User


class ExcelWorksheetSanitizationUnitTest(TestCase):
    """Unit tests for sanitize_worksheet_name and safe_add_worksheet helper."""

    def test_replaces_forbidden_chars(self):
        # Forbidden characters: \ / : * ? [ ]
        raw = r"Test [1]: *special? / \ names"
        sanitized = sanitize_worksheet_name(raw)
        self.assertNotIn("/", sanitized)
        self.assertNotIn("\\", sanitized)
        self.assertNotIn(":", sanitized)
        self.assertNotIn("*", sanitized)
        self.assertNotIn("?", sanitized)
        self.assertNotIn("[", sanitized)
        self.assertNotIn("]", sanitized)
        self.assertLessEqual(len(sanitized), 31)

    def test_products_report_label_sanitization(self):
        label = "Mahsulotlar / inventar hisoboti"
        sanitized = sanitize_worksheet_name(label)
        self.assertEqual(sanitized, "Mahsulotlar - inventar hisoboti")
        self.assertEqual(len(sanitized), 31)
        self.assertNotIn("/", sanitized)

    def test_max_length_limit_31(self):
        long_name = "A" * 60
        sanitized = sanitize_worksheet_name(long_name)
        self.assertEqual(len(sanitized), 31)
        self.assertEqual(sanitized, "A" * 31)

    def test_apostrophe_handling(self):
        # Excel disallows sheet names starting or ending with an apostrophe
        name = "'MySheet'"
        sanitized = sanitize_worksheet_name(name)
        self.assertEqual(sanitized, "MySheet")
        self.assertFalse(sanitized.startswith("'"))
        self.assertFalse(sanitized.endswith("'"))

        # Internal apostrophe is allowed
        name_with_internal = "Do'kon hisoboti"
        sanitized_internal = sanitize_worksheet_name(name_with_internal)
        self.assertEqual(sanitized_internal, "Do'kon hisoboti")

        # Trailing apostrophe when sliced at 31 chars
        name_cutoff = "123456789012345678901234567890'extra"
        sanitized_cutoff = sanitize_worksheet_name(name_cutoff)
        self.assertLessEqual(len(sanitized_cutoff), 31)
        self.assertFalse(sanitized_cutoff.endswith("'"))

    def test_empty_none_and_all_forbidden(self):
        self.assertEqual(sanitize_worksheet_name(""), "Hisobot")
        self.assertEqual(sanitize_worksheet_name(None), "Hisobot")
        self.assertEqual(sanitize_worksheet_name("   "), "Hisobot")
        self.assertEqual(sanitize_worksheet_name("[]:*?/\\"), "Hisobot")
        self.assertEqual(sanitize_worksheet_name("'''''"), "Hisobot")

    def test_duplicate_names_resolution(self):
        existing = {"hisobot"}
        res1 = sanitize_worksheet_name("Hisobot", existing_names=existing)
        self.assertEqual(res1, "Hisobot_1")

        existing.add(res1.lower())
        res2 = sanitize_worksheet_name("Hisobot", existing_names=existing)
        self.assertEqual(res2, "Hisobot_2")

    def test_duplicate_name_with_31_char_limit(self):
        name_31 = "A" * 31
        existing = {name_31.lower()}
        res = sanitize_worksheet_name(name_31, existing_names=existing)
        self.assertLessEqual(len(res), 31)
        self.assertTrue(res.endswith("_1"))
        self.assertNotIn(res.lower(), existing)

    def test_case_insensitive_duplicate(self):
        existing = {"sales_data"}
        res = sanitize_worksheet_name("SALES_DATA", existing_names=existing)
        self.assertEqual(res, "SALES_DATA_1")

    def test_safe_add_worksheet_in_xlsxwriter(self):
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf)

        ws1 = safe_add_worksheet(wb, "Mahsulotlar / inventar hisoboti")
        self.assertEqual(ws1.name, "Mahsulotlar - inventar hisoboti")

        # Add duplicate
        ws2 = safe_add_worksheet(wb, "Mahsulotlar / inventar hisoboti")
        self.assertEqual(ws2.name, "Mahsulotlar - inventar hisobo_1")

        # Add forbidden characters
        ws3 = safe_add_worksheet(wb, r"[:*?/\\]")
        self.assertTrue(ws3.name.startswith("Hisobot"))

        wb.close()
        buf.seek(0)
        # Verify openpyxl can load the workbook
        wb_in = openpyxl.load_workbook(buf)
        sheet_names = wb_in.sheetnames
        self.assertIn("Mahsulotlar - inventar hisoboti", sheet_names)
        self.assertIn("Mahsulotlar - inventar hisobo_1", sheet_names)


class ReportExcelExportIntegrationTest(TestCase):
    """
    Integration tests for Excel export across all 9 report types:
    - products
    - imports
    - supplier_sales
    - order_returns
    - write_offs
    - inventory_results
    - sales_by_product
    - product_efficiency
    - abc_analysis
    """

    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(
            name="Asosiy do'kon", address="Toshkent", phone_number="+998901234567"
        )
        cls.role = Role.objects.create(name="Super Admin", permissions=["*"])
        cls.user = User.objects.create(
            phone_number="+998901112233",
            email="admin@test.uz",
            is_superuser=True,
            is_staff=True,
            role=cls.role,
        )
        StoreUser.objects.create(store=cls.store, user=cls.user, role=cls.role)

        cls.unit = ProductUnitMeasurement.objects.create(measurement="dona")
        cls.category = Category.objects.create(name="Elektronika")
        cls.brand = Brand.objects.create(name="Apple")
        cls.product = Product.objects.create(
            name="iPhone 15 Pro",
            category=cls.category,
            brand=cls.brand,
            unit_measurement=cls.unit,
            min_stock=5,
            status=Product.ProductStatus.ACTIVE,
        )

    def setUp(self):
        self.factory = APIRequestFactory()

    def _export_excel(self, report_type: str, extra_params: dict | None = None):
        params = {
            "report_type": report_type,
            "export_type": "excel",
            "from": (timezone.localdate() - timedelta(days=30)).strftime("%Y-%m-%d"),
            "to": timezone.localdate().strftime("%Y-%m-%d"),
        }
        if extra_params:
            params.update(extra_params)

        request = self.factory.get("/api/reports/builder/export/", params)
        force_authenticate(request, user=self.user)
        view = ReportBuilderExportAPIView.as_view()
        response = view(request)
        return response

    def test_export_excel_all_nine_reports(self):
        report_types = [
            "products",
            "imports",
            "supplier_sales",
            "order_returns",
            "write_offs",
            "inventory_results",
            "sales_by_product",
            "product_efficiency",
            "abc_analysis",
        ]

        for rep in report_types:
            with self.subTest(report_type=rep):
                resp = self._export_excel(rep)
                self.assertEqual(
                    resp.status_code,
                    200,
                    f"Report '{rep}' Excel export failed with status {resp.status_code}: {resp.content[:200]}",
                )
                self.assertEqual(
                    resp["Content-Type"],
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                # Verify standard XLSX magic bytes (ZIP header)
                self.assertTrue(
                    resp.content.startswith(b"PK\x03\x04"),
                    f"Report '{rep}' did not return valid XLSX binary data",
                )

                # Programmatic verification with openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(resp.content))
                self.assertGreater(len(wb.sheetnames), 0)
                for sname in wb.sheetnames:
                    self.assertLessEqual(len(sname), 31)
                    self.assertFalse(sname.startswith("'"))
                    self.assertFalse(sname.endswith("'"))
                    for bad_char in "[]:*?/\\":
                        self.assertNotIn(bad_char, sname)

    def test_products_excel_export_structure_verification(self):
        resp = self._export_excel("products")
        self.assertEqual(resp.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        self.assertEqual(wb.sheetnames[0], "Mahsulotlar - inventar hisoboti")

        ws = wb["Mahsulotlar - inventar hisoboti"]
        # Title banner on row 1
        self.assertIn("Mahsulotlar", str(ws.cell(row=1, column=1).value))
        # Ensure there are rows
        self.assertGreater(ws.max_row, 3)
        self.assertGreater(ws.max_column, 2)
