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
from apps.products.models import Brand, Category, Product, ProductBatch, ProductUnitMeasurement
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


class ReportExcelDesignAndOptimizationTest(TestCase):
    """
    Comprehensive tests verifying the unified, clean, and optimized Excel design:
    1. Pure white data cells (no zebra / banded rows / dark stripes)
    2. Excel Table feature present with AutoFilter
    3. tableStyleInfo.showRowStripes is False (banded_rows=False)
    4. Optimal content-aware column widths (no excessive empty space, long text safely capped)
    5. Freeze panes preserved
    6. Verified across simple, multi-column, long-text, and large dataset reports
    """

    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(
            name="Asosiy Markaziy Do'kon", address="Toshkent shahri", phone_number="+998901234567"
        )
        cls.role = Role.objects.create(name="Super Admin", permissions=["*"])
        cls.user = User.objects.create(
            phone_number="+998909998877",
            email="reportadmin@test.uz",
            is_superuser=True,
            is_staff=True,
            role=cls.role,
        )
        StoreUser.objects.create(store=cls.store, user=cls.user, role=cls.role)

        cls.unit = ProductUnitMeasurement.objects.create(measurement="dona")
        cls.category = Category.objects.create(name="Avto Ehtiyot Qismlari")
        cls.brand = Brand.objects.create(name="Bosch Avto")

        # Standard product
        cls.prod1 = Product.objects.create(
            name="Old tormoz kalodkasi Bosch",
            sku="BOSCH-BP-001",
            barcode="4780001001001",
            category=cls.category,
            brand=cls.brand,
            unit_measurement=cls.unit,
            min_stock=5,
            status=Product.ProductStatus.ACTIVE,
        )
        ProductBatch.objects.create(
            product=cls.prod1, store=cls.store, quantity=25,
            purchase_price=Decimal("120000.00"), selling_price=Decimal("180000.00"),
        )

        # Product with long name to test text width capping (max_width=40)
        cls.long_text_name = "Original amortizator podveskasi chap tomon gidravlik yuqori sifatli germaniya model 2026"
        cls.prod_long = Product.objects.create(
            name=cls.long_text_name,
            sku="AMZ-LONG-999",
            barcode="4780001001002",
            category=cls.category,
            brand=cls.brand,
            unit_measurement=cls.unit,
            min_stock=2,
            status=Product.ProductStatus.ACTIVE,
        )
        ProductBatch.objects.create(
            product=cls.prod_long, store=cls.store, quantity=10,
            purchase_price=Decimal("450000.00"), selling_price=Decimal("600000.00"),
        )

        # Bulk products for large dataset testing
        bulk_prods = []
        for i in range(1, 40):
            p = Product(
                name=f"Ehtiyot qism model #{i}",
                sku=f"SKU-{i:04d}",
                barcode=f"478000100{i:04d}",
                category=cls.category,
                brand=cls.brand,
                unit_measurement=cls.unit,
                min_stock=3,
                status=Product.ProductStatus.ACTIVE,
            )
            bulk_prods.append(p)
        Product.objects.bulk_create(bulk_prods)

        bulk_batches = [
            ProductBatch(
                product=p, store=cls.store, quantity=Decimal(i * 2),
                purchase_price=Decimal("50000.00"), selling_price=Decimal("75000.00"),
            )
            for i, p in enumerate(Product.objects.filter(sku__startswith="SKU-"), start=1)
        ]
        ProductBatch.objects.bulk_create(bulk_batches)

    def setUp(self):
        self.factory = APIRequestFactory()

    def _get_excel(self, report_type: str, extra_params: dict | None = None):
        params = {
            "report_type": report_type,
            "export_type": "excel",
            "from": (timezone.localdate() - timedelta(days=30)).strftime("%Y-%m-%d"),
            "to": timezone.localdate().strftime("%Y-%m-%d"),
        }
        if extra_params:
            params.update(extra_params)
        req = self.factory.get("/api/reports/builder/export/", params)
        force_authenticate(req, user=self.user)
        resp = ReportBuilderExportAPIView.as_view()(req)
        return resp

    def test_a_products_report_design_and_no_banded_rows(self):
        """A. Simple report: products Excel export structure, white background, no banded rows."""
        resp = self._get_excel("products")
        self.assertEqual(resp.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        self.assertIsNotNone(ws)

        # 1. OpenXML Table exists and has AutoFilter
        self.assertGreater(len(ws.tables), 0)
        table = list(ws.tables.values())[0]
        self.assertIsNotNone(table.autoFilter)

        # 2. Banded rows strictly disabled
        self.assertFalse(table.tableStyleInfo.showRowStripes)
        self.assertFalse(table.tableStyleInfo.showColumnStripes)

        # 3. Freeze panes present
        self.assertIsNotNone(ws.freeze_panes)

        # 4. Column dimensions: check optimal widths
        for col_letter, col_dim in ws.column_dimensions.items():
            if col_dim.width is not None:
                self.assertGreaterEqual(col_dim.width, 8)
                self.assertLessEqual(col_dim.width, 45)

        # 5. Data rows have white/clean background (no dark/zebra fills)
        header_row = 4  # rows after title and meta
        for r in range(header_row + 1, min(ws.max_row + 1, header_row + 10)):
            for c in range(1, ws.max_column + 1):
                cell = ws.cell(row=r, column=c)
                fill = cell.fill
                # Fill should either be None/empty or pure white
                if fill and fill.fill_type:
                    color = getattr(fill.fgColor, "rgb", None)
                    if color:
                        self.assertIn(str(color).upper(), ("FFFFFFFF", "00FFFFFF", "00000000"))

    def test_b_multi_column_report_imports_and_supplier_sales(self):
        """B. Multi-column report: imports and supplier_sales."""
        # Test imports
        resp_imp = self._get_excel("imports")
        self.assertEqual(resp_imp.status_code, 200)
        wb_imp = openpyxl.load_workbook(io.BytesIO(resp_imp.content))
        ws_imp = wb_imp.active
        self.assertGreater(len(ws_imp.tables), 0)
        tbl_imp = list(ws_imp.tables.values())[0]
        self.assertFalse(tbl_imp.tableStyleInfo.showRowStripes)
        self.assertIsNotNone(tbl_imp.autoFilter)
        self.assertIsNotNone(ws_imp.freeze_panes)

        # Test supplier_sales
        resp_ss = self._get_excel("supplier_sales")
        self.assertEqual(resp_ss.status_code, 200)
        wb_ss = openpyxl.load_workbook(io.BytesIO(resp_ss.content))
        ws_ss = wb_ss.active
        self.assertGreater(len(ws_ss.tables), 0)
        tbl_ss = list(ws_ss.tables.values())[0]
        self.assertFalse(tbl_ss.tableStyleInfo.showRowStripes)
        self.assertIsNotNone(tbl_ss.autoFilter)
        self.assertIsNotNone(ws_ss.freeze_panes)

    def test_c_long_text_does_not_stretch_worksheet(self):
        """C. Long text report: ensures worksheet is not overly stretched (capped max width)."""
        resp = self._get_excel("products")
        self.assertEqual(resp.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active

        # Check all column widths
        for col_letter, col_dim in ws.column_dimensions.items():
            if col_dim.width is not None:
                # Even for the 140+ character product name, width must be capped <= 42
                self.assertLessEqual(col_dim.width, 42, f"Column {col_letter} is too wide: {col_dim.width}")

        # Ensure long text was correctly written into cell
        found_long = False
        for r in range(1, ws.max_row + 1):
            val = str(ws.cell(row=r, column=1).value or "")
            if "Original amortizator" in val:
                found_long = True
                self.assertEqual(val, self.long_text_name)
                break
        self.assertTrue(found_long, "Long text product was preserved in cell content")

    def test_d_large_dataset_stock_leftovers(self):
        """D. Large dataset report: stock_leftovers with 40+ rows."""
        resp = self._get_excel("stock_leftovers")
        self.assertEqual(resp.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        self.assertGreater(len(ws.tables), 0)
        table = list(ws.tables.values())[0]
        self.assertFalse(table.tableStyleInfo.showRowStripes)
        self.assertIsNotNone(ws.freeze_panes)
        self.assertGreater(ws.max_row, 30)

    def test_e_sales_multi_sheet_design(self):
        """E. Sales report multi-sheet: Cheklar and Mahsulotlar both have clean tables."""
        resp = self._get_excel("sales")
        self.assertEqual(resp.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        self.assertIn("Cheklar", wb.sheetnames)
        self.assertIn("Mahsulotlar", wb.sheetnames)

        for sname in ("Cheklar", "Mahsulotlar"):
            ws = wb[sname]
            self.assertGreater(len(ws.tables), 0, f"Sheet '{sname}' must contain an Excel table")
            tbl = list(ws.tables.values())[0]
            self.assertFalse(tbl.tableStyleInfo.showRowStripes)
            self.assertIsNotNone(tbl.autoFilter)
            self.assertIsNotNone(ws.freeze_panes)

    def test_f_dashboard_excel_export_clean_palette(self):
        """F. Dashboard report: /api/reports/export/ has no zebra rows and clean styling."""
        from apps.reports.views.export_view import ReportsExcelExportAPIView
        req = self.factory.get("/api/reports/export/")
        force_authenticate(req, user=self.user)
        resp = ReportsExcelExportAPIView.as_view()(req)
        self.assertEqual(resp.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        self.assertGreater(len(wb.sheetnames), 0)
        for sname in wb.sheetnames:
            ws = wb[sname]
            # Verify data cell fills are white / none (no zebra striping)
            for r in range(4, min(ws.max_row + 1, 15)):
                for c in range(1, min(ws.max_column + 1, 8)):
                    cell = ws.cell(row=r, column=c)
                    fill = cell.fill
                    if fill and fill.fill_type:
                        color = getattr(fill.fgColor, "rgb", None)
                        if color:
                            # F5F7FA (old zebra) must NOT exist
                            self.assertNotEqual(str(color).upper(), "FFF5F7FA")

