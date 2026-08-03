"""
Reports moduli — "Mahsulot tarixi" hisoboti uchun testlar.

Ishga tushirish:
    python manage.py test apps.reports
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.contract.models import StockEntry, StockEntryItem, Supplier
from apps.products.models import Product, ProductBatch
from apps.reports.services.report_builder import ReportBuilderService
from apps.reports.views.report_builder_view import (
    ReportBuilderExportAPIView,
    ReportBuilderGenerateAPIView,
)
from apps.sales.models import Sale, SaleItem
from apps.store.models import Store, StoreUser
from apps.users.models.user import User

PARAMS = {"report_type": "product_history"}


class ProductHistoryReportTest(TestCase):
    """Kartochka (info) + harakatlar jadvali + filtrlar + eksport."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(
            phone_number="+998900000101", email="admin@reports.uz",
            is_superuser=True, is_staff=True,
        )
        cls.store = Store.objects.create(
            name="Markaziy do'kon", phone_number="+998900000102",
            address="Test", type=Store.StoreType.STORE,
        )
        cls.other_store = Store.objects.create(
            name="Filial", phone_number="+998900000103",
            address="Test", type=Store.StoreType.STORE,
        )
        cls.supplier = Supplier.objects.create(
            name="Ta'minotchi", phone_number="+998900000104", description="", address="",
        )
        cls.product = Product.objects.create(name="Moy filtri", min_stock=5)
        cls.other_product = Product.objects.create(name="Havo filtri")

        ProductBatch.objects.create(
            product=cls.product, store=cls.store, quantity=8,
            purchase_price=Decimal("50"), selling_price=Decimal("100"),
        )

        # Kirim: 10 dona × 50
        entry = StockEntry.objects.create(
            supplier=cls.supplier, store=cls.store, total_amount=Decimal("500"),
            cash_amount=Decimal("500"),
        )
        StockEntryItem.objects.create(
            entry=entry, product=cls.product, quantity=10,
            purchase_price=Decimal("50"), selling_price=Decimal("100"),
        )
        # Sotuv: 2 dona × 100 (tannarx 50 → foyda 100)
        sale = Sale.objects.create(
            store=cls.store, seller=cls.admin, total_amount=Decimal("200"),
            paid_amount=Decimal("200"), status=Sale.Status.PAID,
        )
        SaleItem.objects.create(
            sale=sale, product=cls.product, quantity=2,
            purchase_price=Decimal("50"), unit_price=Decimal("100"),
            total_price=Decimal("200"),
        )
        # Boshqa mahsulot sotuvi — hisobotga tushmasligi kerak
        SaleItem.objects.create(
            sale=sale, product=cls.other_product, quantity=1,
            purchase_price=Decimal("10"), unit_price=Decimal("20"),
            total_price=Decimal("20"),
        )

    def generate(self, **extra):
        return ReportBuilderService.generate({**PARAMS, **extra}, self.admin)

    # ── Meta ──────────────────────────────────────────────

    def test_meta_exposes_required_product_filter(self):
        spec = next(
            r for r in ReportBuilderService.meta()["reports"] if r["key"] == "product_history"
        )
        product_filter = next(f for f in spec["filters"] if f["param"] == "product_id")
        self.assertEqual(product_filter["type"], "product")
        self.assertTrue(product_filter["required"])
        # Katalog katta — variantlar meta bilan yuborilmaydi
        self.assertNotIn("options", product_filter)

    # ── Validatsiya ───────────────────────────────────────

    def test_product_is_required(self):
        with self.assertRaises(ValidationError) as ctx:
            self.generate()
        self.assertIn("product_id", ctx.exception.detail)

    def test_unknown_product(self):
        with self.assertRaises(ValidationError):
            self.generate(product_id="999999")

    # ── Natija ────────────────────────────────────────────

    def test_report_contains_only_selected_product_events(self):
        data = self.generate(product_id=str(self.product.id))

        self.assertEqual(data["total"], 2)  # kirim + sotuv
        events = sorted(row["event"] for row in data["rows"])
        self.assertEqual(events, ["Kirim", "Sotuv"])
        sale_row = next(r for r in data["rows"] if r["event"] == "Sotuv")
        self.assertEqual(sale_row["quantity"], 2)
        self.assertEqual(sale_row["amount"], "200.00")
        self.assertEqual(sale_row["store"], "Markaziy do'kon")
        self.assertEqual(sale_row["status"], "To'langan")

    def test_summary_and_info_card(self):
        data = self.generate(product_id=str(self.product.id))

        summary = {s["label"]: s["value"] for s in data["summary"]}
        self.assertEqual(summary["Kirim (dona)"], 10)
        self.assertEqual(summary["Sotilgan (dona)"], 2)
        self.assertEqual(summary["Sotuv summasi"], "200.00")
        self.assertEqual(summary["Foyda"], "100.00")
        self.assertEqual(summary["Joriy qoldiq"], 8)

        info = data["info"]
        self.assertEqual(info["title"], "Moy filtri")
        self.assertIn(self.product.sku, info["subtitle"])
        fields = {f["label"]: f["value"] for f in info["fields"]}
        self.assertEqual(fields["Joriy qoldiq"], 8)
        self.assertEqual(fields["Minimal qoldiq"], 5)
        self.assertEqual(fields["O'rtacha kirim narxi"], "50.00")
        self.assertEqual(fields["Kirimlar soni"], 1)
        self.assertEqual(fields["Harakatlar soni"], 2)

    def test_event_type_filter(self):
        data = self.generate(product_id=str(self.product.id), event_type="sale")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["rows"][0]["event"], "Sotuv")

    def test_store_filter_excludes_other_stores(self):
        data = self.generate(product_id=str(self.product.id), store_id=str(self.other_store.id))
        self.assertEqual(data["total"], 0)

    def test_date_filter(self):
        # Kelajakdagi kun — hech qanday harakat tushmaydi
        data = self.generate(
            product_id=str(self.product.id), **{"from": "2099-01-01", "to": "2099-01-02"}
        )
        self.assertEqual(data["total"], 0)
        # Sanasiz — butun tarix (oxirgi 30 kun bilan cheklanmaydi)
        self.assertEqual(self.generate(product_id=str(self.product.id))["total"], 2)

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.generate(product_id=str(self.product.id), **{"from": "01.01.2026"})

    # ── Eksport ───────────────────────────────────────────

    def test_export_matches_table_and_carries_info(self):
        label, columns, rows, summary, info = ReportBuilderService.export_rows(
            {**PARAMS, "product_id": str(self.product.id)}, self.admin
        )
        self.assertIn("Moy filtri", label)  # fayl sarlavhasida mahsulot nomi
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(columns), 12)
        self.assertIsNotNone(info)
        self.assertEqual(info["title"], "Moy filtri")
        # Eksport jadval bilan bir xil filtrdan o'tadi
        self.assertEqual({s["label"]: s["value"] for s in summary}["Sotilgan (dona)"], 2)

    def _call(self, view, params):
        request = APIRequestFactory().get("/api/reports/builder/", params)
        force_authenticate(request, user=self.admin)
        return view.as_view()(request)

    def test_export_view_builds_excel_and_csv(self):
        """Kartochka bloki fayl ichiga tushadi — jadval satrlari surilib ketmaydi."""
        params = {**PARAMS, "product_id": str(self.product.id)}

        excel = self._call(ReportBuilderExportAPIView, {**params, "export_type": "excel"})
        self.assertEqual(excel.status_code, 200)
        self.assertIn("spreadsheetml", excel["Content-Type"])
        self.assertGreater(len(excel.content), 1000)

        csv_response = self._call(ReportBuilderExportAPIView, {**params, "export_type": "csv"})
        self.assertEqual(csv_response.status_code, 200)
        text = csv_response.content.decode("utf-8")
        self.assertIn("Moy filtri", text)       # kartochka sarlavhasi
        self.assertIn("Joriy qoldiq", text)     # kartochka maydonlari
        self.assertIn("Hujjat", text)           # jadval sarlavhasi
        self.assertIn("Sotuv", text)            # harakat qatori

    def test_generate_view_returns_400_without_product(self):
        response = self._call(ReportBuilderGenerateAPIView, PARAMS)
        self.assertEqual(response.status_code, 400)
        self.assertIn("product_id", response.data)

    # ── Do'kon ruxsati ────────────────────────────────────

    def test_store_staff_sees_only_own_store(self):
        """Xodim boshqa do'konning harakatlarini ko'rmaydi (fail-closed)."""
        staff = User.objects.create(phone_number="+998900000105", full_name="Sotuvchi")
        StoreUser.objects.create(
            user=staff, store=self.other_store, role=StoreUser.Role.Manager, is_active=True
        )
        data = ReportBuilderService.generate(
            {**PARAMS, "product_id": str(self.product.id)}, staff
        )
        self.assertEqual(data["total"], 0)
        self.assertEqual(
            {s["label"]: s["value"] for s in data["summary"]}["Sotilgan (dona)"], 0
        )
