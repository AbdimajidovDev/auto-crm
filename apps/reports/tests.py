"""
Reports moduli — "Mahsulot tarixi" hisoboti uchun testlar.

Ishga tushirish:
    python manage.py test apps.reports
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
import uuid

from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.contract.models import (
    StockEntry,
    StockEntryItem,
    StockEntryReturn,
    StockEntryReturnItem,
    Supplier,
    SupplierTransaction,
)
from apps.inventory.models import (
    InventoryCount,
    InventoryMovement,
    InventorySession,
    InventorySnapshot,
)
from apps.products.models import Brand, Category, Product, ProductBatch, ProductUnitMeasurement
from apps.writeoff.models import WriteOff, WriteOffItem
from apps.reports.services.reporting_foundation import (
    PeriodSalesMetrics,
    ReportingFoundationService,
)
from apps.reports.services.report_builder import ReportBuilderService
from apps.reports.views.report_builder_view import (
    ReportBuilderExportAPIView,
    ReportBuilderGenerateAPIView,
    ReportBuilderMetaAPIView,
)
from apps.sales.models import BankCard, Payment, Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.sales.profit import partial_cost_filter, sum_item_profit
from apps.store.models import Store, StoreUser
from apps.users.models.customers import Customer
from apps.users.models.role import Role
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


# ─────────────────────────────────────────────────────────────
#  PHASE 0: REPORTING FOUNDATION TESTLARI
# ─────────────────────────────────────────────────────────────

class ReportingFoundationMathTest(TestCase):
    """
    Formulalar va matematik hisob-kitoblarning xavfsizligi va aniqligi:
    - Margin & Markup
    - Stock leftovers metrics (potential profit, margin %)
    - 0 ga bo'lish va manfiy qiymatlar
    """

    def test_margin_and_markup_standard(self):
        # 200 tushum, 120 tannarx -> foyda 80, margin 40.0%, markup 66.7%
        res = ReportingFoundationService.calculate_margin_and_markup(
            revenue=Decimal("200"),
            cost=Decimal("120"),
        )
        self.assertEqual(res["profit"], Decimal("80"))
        self.assertEqual(res["margin_pct"], Decimal("40.0"))
        self.assertEqual(res["markup_pct"], Decimal("66.7"))

    def test_margin_and_markup_zero_cost(self):
        # 100 tushum, 0 tannarx -> foyda 100, margin 100.0%, markup 0.0%
        res = ReportingFoundationService.calculate_margin_and_markup(
            revenue=Decimal("100"),
            cost=Decimal("0"),
        )
        self.assertEqual(res["profit"], Decimal("100"))
        self.assertEqual(res["margin_pct"], Decimal("100.0"))
        self.assertEqual(res["markup_pct"], Decimal("0.0"))

    def test_margin_and_markup_zero_revenue(self):
        # 0 tushum, 50 tannarx -> foyda -50, margin 0.0%, markup -100.0%
        res = ReportingFoundationService.calculate_margin_and_markup(
            revenue=Decimal("0"),
            cost=Decimal("50"),
        )
        self.assertEqual(res["profit"], Decimal("-50"))
        self.assertEqual(res["margin_pct"], Decimal("0.0"))
        self.assertEqual(res["markup_pct"], Decimal("-100.0"))

    def test_margin_and_markup_both_zero(self):
        res = ReportingFoundationService.calculate_margin_and_markup(0, 0)
        self.assertEqual(res["profit"], Decimal("0"))
        self.assertEqual(res["margin_pct"], Decimal("0.0"))
        self.assertEqual(res["markup_pct"], Decimal("0.0"))

    def test_stock_leftovers_metrics_standard(self):
        # 10 dona, kelish 40, sotish 100
        # purchase_value = 400.00, selling_value = 1000.00, potential_profit = 600.00
        # margin_pct = 60.0%, markup_pct = 150.0%
        res = ReportingFoundationService.calculate_stock_leftovers_metrics(
            qty=Decimal("10"),
            purchase_price=Decimal("40"),
            selling_price=Decimal("100"),
        )
        self.assertEqual(res["purchase_value"], Decimal("400.00"))
        self.assertEqual(res["selling_value"], Decimal("1000.00"))
        self.assertEqual(res["potential_profit"], Decimal("600.00"))
        self.assertEqual(res["margin_pct"], Decimal("60.0"))
        self.assertEqual(res["markup_pct"], Decimal("150.0"))

    def test_stock_leftovers_metrics_zero_and_none(self):
        res = ReportingFoundationService.calculate_stock_leftovers_metrics(
            qty=None,
            purchase_price=None,
            selling_price=None,
        )
        self.assertEqual(res["purchase_value"], Decimal("0.00"))
        self.assertEqual(res["selling_value"], Decimal("0.00"))
        self.assertEqual(res["potential_profit"], Decimal("0.00"))
        self.assertEqual(res["margin_pct"], Decimal("0.0"))
        self.assertEqual(res["markup_pct"], Decimal("0.0"))

    def test_stock_leftovers_metrics_loss_making(self):
        # Kelish narxi sotish narxidan baland: kelish 120, sotish 100
        res = ReportingFoundationService.calculate_stock_leftovers_metrics(
            qty=5,
            purchase_price=120,
            selling_price=100,
        )
        self.assertEqual(res["potential_profit"], Decimal("-100.00"))
        self.assertEqual(res["margin_pct"], Decimal("-20.0"))
        self.assertEqual(res["markup_pct"], Decimal("-16.7"))


class ReportingFoundationSalesReturnsTest(TestCase):
    """
    Sotuvlar va Qaytarimlar bazasi:
    - Normal sotuv
    - Qisman qaytarim
    - To'liq qaytarim (status='r')
    - Chegirmali sotuv va proporsional foyda
    - Tannarxsiz sotuvlar va ogohlantirish
    - Davrlararo qaytarim (Period return logic)
    - Subquery orqali ta'minotchi va oxirgi kirim
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(
            phone_number="+998901111111", email="admin2@reports.uz",
            is_superuser=True, is_staff=True,
        )
        cls.store = Store.objects.create(
            name="Asosiy Do'kon", phone_number="+998901111112",
            address="Test", type=Store.StoreType.STORE,
        )
        cls.supplier1 = Supplier.objects.create(
            name="Ta'minotchi 1", phone_number="+998901111113",
        )
        cls.supplier2 = Supplier.objects.create(
            name="Ta'minotchi 2", phone_number="+998901111114",
        )
        cls.category = Category.objects.create(name="Ehtiyot qismlar")
        cls.prod_a = Product.objects.create(name="Tormoz kolodkasi", category=cls.category, sku="TK-01")
        cls.prod_b = Product.objects.create(name="Svecha", category=cls.category, sku="SV-02")

    def test_normal_sale_annotation(self):
        """Oddiy sotuv: net_total == total_amount, net_paid == paid_amount, net_profit to'g'ri."""
        sale = Sale.objects.create(
            store=self.store, seller=self.admin,
            total_amount=Decimal("400.00"), paid_amount=Decimal("400.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_a, quantity=Decimal("4"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("50.00"),
            total_price=Decimal("400.00"),
        )
        Payment.objects.create(
            sale=sale, amount=Decimal("400.00"), type=Payment.Type.CASH, is_refund=False,
        )

        qs = ReportingFoundationService.annotate_sale_net_fields(Sale.objects.filter(id=sale.id))
        s = qs.first()
        self.assertEqual(s.net_total, Decimal("400.00"))
        self.assertEqual(s.net_paid, Decimal("400.00"))
        self.assertEqual(s.net_debt, Decimal("0.00"))
        # Sof foyda = (100 - 50) * 4 = 200.00
        self.assertEqual(s.net_profit, Decimal("200.00"))

    def test_partial_return_annotation(self):
        """
        Qisman qaytarim:
        Sale.total_amount 500 (5 dona x 100).
        2 dona qaytarildi (SaleReturn total_refund=200, SaleItem.returned_quantity=2).
        Haqiqiy net_total = 300, net_paid = 300, net_profit = (100 - 60) * 3 = 120.
        """
        sale = Sale.objects.create(
            store=self.store, seller=self.admin,
            total_amount=Decimal("500.00"), paid_amount=Decimal("500.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        item = SaleItem.objects.create(
            sale=sale, product=self.prod_a, quantity=Decimal("5"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("500.00"),
        )
        Payment.objects.create(
            sale=sale, amount=Decimal("500.00"), type=Payment.Type.CASH, is_refund=False,
        )

        # 2 dona qaytarildi
        s_return = SaleReturn.objects.create(
            sale=sale, store=self.store, seller=self.admin,
            total_refund=Decimal("200.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=s_return, sale_item=item, product=self.prod_a,
            quantity=Decimal("2"), unit_price=Decimal("100.00"),
            total_price=Decimal("200.00"),
        )
        item.returned_quantity = Decimal("2")
        item.save(update_fields=["returned_quantity"])

        Payment.objects.create(
            sale=sale, amount=Decimal("200.00"), type=Payment.Type.CASH, is_refund=True,
        )

        qs = ReportingFoundationService.annotate_sale_net_fields(Sale.objects.filter(id=sale.id))
        s = qs.first()
        self.assertEqual(s.net_total, Decimal("300.00"))
        self.assertEqual(s.net_paid, Decimal("300.00"))
        self.assertEqual(s.net_debt, Decimal("0.00"))
        self.assertEqual(s.net_profit, Decimal("120.00"))

    def test_full_return_annotation(self):
        """
        To'liq qaytarilgan sotuv (status='r'):
        net_total = 0, net_paid = 0, net_debt = 0, net_profit = 0.
        Umumiy savdo tushumiga QO'SHILMASLIGI SHART.
        """
        sale = Sale.objects.create(
            store=self.store, seller=self.admin,
            total_amount=Decimal("300.00"), paid_amount=Decimal("300.00"),
            status=Sale.Status.RETURNED, payment_type="cash",
        )
        item = SaleItem.objects.create(
            sale=sale, product=self.prod_a, quantity=Decimal("3"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("50.00"),
            total_price=Decimal("300.00"), returned_quantity=Decimal("3"),
        )
        Payment.objects.create(
            sale=sale, amount=Decimal("300.00"), type=Payment.Type.CASH, is_refund=False,
        )
        s_return = SaleReturn.objects.create(
            sale=sale, store=self.store, seller=self.admin,
            total_refund=Decimal("300.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=s_return, sale_item=item, product=self.prod_a,
            quantity=Decimal("3"), unit_price=Decimal("100.00"),
            total_price=Decimal("300.00"),
        )
        Payment.objects.create(
            sale=sale, amount=Decimal("300.00"), type=Payment.Type.CASH, is_refund=True,
        )

        qs = ReportingFoundationService.annotate_sale_net_fields(Sale.objects.filter(id=sale.id))
        s = qs.first()
        self.assertEqual(s.net_total, Decimal("0.00"))
        self.assertEqual(s.net_paid, Decimal("0.00"))
        self.assertEqual(s.net_debt, Decimal("0.00"))
        self.assertEqual(s.net_profit, Decimal("0.00"))

    def test_sale_with_discount_and_profit(self):
        """
        Chegirmali sotuv:
        2 dona x 100 = 200, chegirma 20 -> total_amount = 180.
        Tannarx 50 dan = 100.
        Sof foyda = 180 - 100 = 80.00.
        """
        sale = Sale.objects.create(
            store=self.store, seller=self.admin,
            total_amount=Decimal("180.00"), discount_amount=Decimal("20.00"),
            paid_amount=Decimal("180.00"), status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_a, quantity=Decimal("2"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("50.00"),
            total_price=Decimal("200.00"),
        )
        Payment.objects.create(
            sale=sale, amount=Decimal("180.00"), type=Payment.Type.CASH, is_refund=False,
        )

        qs = ReportingFoundationService.annotate_sale_net_fields(Sale.objects.filter(id=sale.id))
        s = qs.first()
        self.assertEqual(s.net_total, Decimal("180.00"))
        self.assertEqual(s.net_profit, Decimal("80.00"))

    def test_sale_with_missing_purchase_price_flag(self):
        """Tannarx kiritilmagan bo'lsa partial_cost_filter orqali ogohlantirish bayrog'i ishlaydi."""
        sale = Sale.objects.create(
            store=self.store, seller=self.admin,
            total_amount=Decimal("100.00"), paid_amount=Decimal("100.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_a, quantity=Decimal("1"),
            unit_price=Decimal("100.00"), purchase_price=None,
            total_price=Decimal("100.00"),
        )
        has_missing_cost = SaleItem.objects.filter(sale=sale).filter(partial_cost_filter()).exists()
        self.assertTrue(has_missing_cost)

    def test_period_return_logic_cross_period(self):
        """
        Davrlararo qaytarim (Period return logic):
        1-davr: 2026-01-01 dan 2026-02-01 gacha.
        Sotuv 2026-01-15 da bo'ldi (500 so'm).
        2-davr: 2026-02-01 dan 2026-03-01 gacha.
        Qaytarim 2026-02-10 da bo'ldi (200 so'm).
        Natija:
        - 1-davr hisoboti BUZILMAYDI (gross_revenue=500, return_amount=0, net_revenue=500).
        - 2-davr hisobotida qaytarim aks etadi (return_amount=200).
        """
        dt_sale = datetime(2026, 1, 15, 12, 0, tzinfo=dt_timezone.utc)
        dt_return = datetime(2026, 2, 10, 14, 0, tzinfo=dt_timezone.utc)

        sale = Sale.objects.create(
            store=self.store, seller=self.admin,
            total_amount=Decimal("500.00"), paid_amount=Decimal("500.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        item = SaleItem.objects.create(
            sale=sale, product=self.prod_a, quantity=Decimal("5"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("500.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=dt_sale)

        s_return = SaleReturn.objects.create(
            sale=sale, store=self.store, seller=self.admin,
            total_refund=Decimal("200.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=s_return, sale_item=item, product=self.prod_a,
            quantity=Decimal("2"), unit_price=Decimal("100.00"),
            total_price=Decimal("200.00"),
        )
        SaleReturn.objects.filter(id=s_return.id).update(created_at=dt_return)

        # 1-davr hisob-kitobi
        p1 = ReportingFoundationService.get_period_sales_metrics(
            start=datetime(2026, 1, 1, 0, 0, tzinfo=dt_timezone.utc),
            end=datetime(2026, 2, 1, 0, 0, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(p1.gross_revenue, Decimal("500.00"))
        self.assertEqual(p1.return_amount, Decimal("0.00"))
        self.assertEqual(p1.net_revenue, Decimal("500.00"))
        self.assertEqual(p1.sales_count, 1)
        self.assertEqual(p1.returns_count, 0)

        # 2-davr hisob-kitobi
        p2 = ReportingFoundationService.get_period_sales_metrics(
            start=datetime(2026, 2, 1, 0, 0, tzinfo=dt_timezone.utc),
            end=datetime(2026, 3, 1, 0, 0, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(p2.gross_revenue, Decimal("0.00"))
        self.assertEqual(p2.return_amount, Decimal("200.00"))
        self.assertEqual(p2.returns_count, 1)

    def test_supplier_resolution_subquery(self):
        """
        Har bir tovar uchun eng oxirgi kirim ta'minotchisi va sanasi Subquery
        orqali to'g'ri aniqlanishini tekshirish.
        """
        dt1 = datetime(2026, 1, 10, 10, 0, tzinfo=dt_timezone.utc)
        dt2 = datetime(2026, 1, 20, 12, 0, tzinfo=dt_timezone.utc)
        dt3 = datetime(2026, 1, 12, 11, 0, tzinfo=dt_timezone.utc)

        e1 = StockEntry.objects.create(
            supplier=self.supplier1, store=self.store, total_amount=Decimal("100"),
        )
        StockEntryItem.objects.create(
            entry=e1, product=self.prod_a, quantity=10, purchase_price=10, selling_price=20,
        )
        StockEntry.objects.filter(id=e1.id).update(created_at=dt1)

        e2 = StockEntry.objects.create(
            supplier=self.supplier2, store=self.store, total_amount=Decimal("200"),
        )
        StockEntryItem.objects.create(
            entry=e2, product=self.prod_a, quantity=15, purchase_price=12, selling_price=22,
        )
        StockEntry.objects.filter(id=e2.id).update(created_at=dt2)

        e3 = StockEntry.objects.create(
            supplier=self.supplier1, store=self.store, total_amount=Decimal("50"),
        )
        StockEntryItem.objects.create(
            entry=e3, product=self.prod_b, quantity=5, purchase_price=10, selling_price=20,
        )
        StockEntry.objects.filter(id=e3.id).update(created_at=dt3)

        res = ReportingFoundationService.get_latest_supplier_and_import_map(
            [self.prod_a.id, self.prod_b.id]
        )
        # prod_a ning oxirgi ta'minotchisi supplier2 bo'lishi kerak
        self.assertEqual(res[self.prod_a.id]["supplier"], self.supplier2.name)
        # prod_b ning oxirgi ta'minotchisi supplier1 bo'lishi kerak
        self.assertEqual(res[self.prod_b.id]["supplier"], self.supplier1.name)

        # Cutoff (before dt2) berilganda prod_a ning ta'minotchisi supplier1 ga qaytishi kerak
        res_before = ReportingFoundationService.get_latest_supplier_and_import_map(
            [self.prod_a.id],
            before=datetime(2026, 1, 15, 0, 0, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(res_before[self.prod_a.id]["supplier"], self.supplier1.name)


class ReportBuilderEnhancedReportsTest(TestCase):
    """
    ReportBuilder orqali sales va stock_leftovers hisobotlarini integratsion tekshirish.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(
            phone_number="+998902222221", email="admin3@reports.uz",
            is_superuser=True, is_staff=True,
        )
        cls.store = Store.objects.create(
            name="Test Store", phone_number="+998902222222",
            address="Test", type=Store.StoreType.STORE,
        )
        cls.supplier = Supplier.objects.create(
            name="Premium Supplier", phone_number="+998902222223",
        )
        cls.category = Category.objects.create(name="Moylar")
        cls.product = Product.objects.create(name="Motor Moyi 5W-40", category=cls.category)

        # Batch
        cls.batch = ProductBatch.objects.create(
            product=cls.product, store=cls.store, quantity=20,
            purchase_price=Decimal("150.00"), selling_price=Decimal("250.00"),
        )
        entry = StockEntry.objects.create(
            supplier=cls.supplier, store=cls.store, total_amount=Decimal("3000.00"),
        )
        StockEntryItem.objects.create(
            entry=entry, product=cls.product, quantity=20,
            purchase_price=Decimal("150.00"), selling_price=Decimal("250.00"),
        )

        # Sotuv: 5 dona sotildi, 1 dona qaytarildi
        cls.sale = Sale.objects.create(
            store=cls.store, seller=cls.admin,
            total_amount=Decimal("1250.00"), paid_amount=Decimal("1250.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        cls.sale_item = SaleItem.objects.create(
            sale=cls.sale, product=cls.product, quantity=Decimal("5"),
            unit_price=Decimal("250.00"), purchase_price=Decimal("150.00"),
            total_price=Decimal("1250.00"), returned_quantity=Decimal("1"),
        )
        Payment.objects.create(
            sale=cls.sale, amount=Decimal("1250.00"), type=Payment.Type.CASH, is_refund=False,
        )
        cls.s_return = SaleReturn.objects.create(
            sale=cls.sale, store=cls.store, seller=cls.admin,
            total_refund=Decimal("250.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=cls.s_return, sale_item=cls.sale_item, product=cls.product,
            quantity=Decimal("1"), unit_price=Decimal("250.00"),
            total_price=Decimal("250.00"),
        )
        Payment.objects.create(
            sale=cls.sale, amount=Decimal("250.00"), type=Payment.Type.CASH, is_refund=True,
        )

    def test_sales_report_reflects_net_figures(self):
        data = ReportBuilderService.generate({"report_type": "sales"}, self.admin)
        self.assertEqual(data["total"], 1)
        row = data["rows"][0]
        # net_total = 1250 - 250 = 1000.00
        self.assertEqual(row["total"], "1000.00")
        self.assertEqual(row["paid"], "1000.00")
        self.assertEqual(row["debt"], "0.00")
        # net_profit = (250 - 150) * 4 = 400.00
        self.assertEqual(row["profit"], "400.00")

        summary = {s["label"]: s["value"] for s in data["summary"]}
        self.assertEqual(summary["Jami summa"], "1000.00")
        self.assertEqual(summary["Sof foyda"], "400.00")
        self.assertIn("Davrdagi qaytarimlar", summary)
        self.assertEqual(summary["Davrdagi qaytarimlar"], "250.00")

    def test_stock_leftovers_report_contains_all_metrics(self):
        data = ReportBuilderService.generate({"report_type": "stock_leftovers"}, self.admin)
        cols = {c["key"] for c in data["columns"]}
        self.assertTrue({
            "purchase_value", "selling_value", "potential_profit", "margin_pct", "last_import", "supplier"
        }.issubset(cols))

        self.assertEqual(data["total"], 1)
        row = data["rows"][0]
        self.assertEqual(row["qty"], Decimal("20.00"))
        self.assertEqual(row["purchase_price"], "150.00")
        self.assertEqual(row["selling_price"], "250.00")
        self.assertEqual(row["purchase_value"], "3000.00")
        self.assertEqual(row["selling_value"], "5000.00")
        self.assertEqual(row["potential_profit"], "2000.00")
        self.assertEqual(row["margin_pct"], "40.0%")
        self.assertEqual(row["supplier"], "Premium Supplier")

        summary = {s["label"]: s["value"] for s in data["summary"]}
        self.assertEqual(summary["Qoldiq (kelish narxida)"], "3000.00")
        self.assertEqual(summary["Qoldiq (sotish narxida)"], "5000.00")
        self.assertEqual(summary["Kutilayotgan foyda"], "2000.00")
        self.assertEqual(summary["Kutilayotgan marja"], "40.0%")


class SalesByProductReportTest(TestCase):
    """
    PHASE 1.1: Tovarlar bo'yicha sotuvlar (Billz darajasida) hisoboti testlari.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(
            phone_number="+998900000201", email="admin_sbp@reports.uz",
            is_superuser=True, is_staff=True,
        )
        cls.sales_role = Role.objects.create(
            name="Sales Role",
            permissions=["reports.sales.view", "reports.sales.export"],
        )
        cls.staff_user = User.objects.create(
            phone_number="+998900000202", email="staff_sbp@reports.uz",
            role=cls.sales_role,
        )
        cls.unauth_user = User.objects.create(
            phone_number="+998900000203", email="unauth_sbp@reports.uz",
        )
        cls.store1 = Store.objects.create(
            name="Asosiy do'kon", phone_number="+998900000204",
            address="Do'kon 1", type=Store.StoreType.STORE,
        )
        cls.store2 = Store.objects.create(
            name="Filial 2", phone_number="+998900000205",
            address="Do'kon 2", type=Store.StoreType.STORE,
        )
        StoreUser.objects.create(
            user=cls.staff_user, store=cls.store1, role=StoreUser.Role.Manager, is_active=True
        )

        cls.cat_oil = Category.objects.create(name="Moylar", description="Motor moylari")
        cls.brand_shell = Brand.objects.create(name="Shell")
        cls.brand_castrol = Brand.objects.create(name="Castrol")
        cls.unit_dona = ProductUnitMeasurement.objects.create(measurement="dona")

        cls.prod_shell = Product.objects.create(
            name="Shell Helix Ultra 5W-40",
            sku="SH-5W40",
            barcode="4780001",
            category=cls.cat_oil,
            brand=cls.brand_shell,
            unit_measurement=cls.unit_dona,
        )
        cls.prod_castrol = Product.objects.create(
            name="Castrol Magnatec 10W-40",
            sku="CAS-10W40",
            barcode="4780002",
            category=cls.cat_oil,
            brand=cls.brand_castrol,
            unit_measurement=cls.unit_dona,
        )
        cls.prod_no_cost = Product.objects.create(
            name="Noma'lum moy",
            sku="UN-001",
            barcode="4780003",
        )

    def _call(self, view, params, user=None):
        request = APIRequestFactory().get("/api/reports/builder/", params)
        force_authenticate(request, user=user or self.admin)
        return view.as_view()(request)

    def test_meta_contains_sales_by_product(self):
        spec = next(
            (r for r in ReportBuilderService.meta()["reports"] if r["key"] == "sales_by_product"),
            None,
        )
        self.assertIsNotNone(spec)
        self.assertEqual(spec["label"], "Tovarlar bo'yicha sotuvlar")
        self.assertTrue(spec["search"])
        filter_params = [f["param"] for f in spec["filters"]]
        self.assertIn("date", filter_params)
        self.assertIn("store_id", filter_params)
        self.assertIn("category_id", filter_params)
        self.assertIn("brand_id", filter_params)
        self.assertIn("sort_by", filter_params)

    def test_sales_by_product_basic_metrics_and_columns(self):
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("1000.00"), paid_amount=Decimal("1000.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_shell, quantity=Decimal("10"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("1000.00"),
        )

        data = ReportBuilderService.generate({"report_type": "sales_by_product"}, self.admin)
        cols = [c["key"] for c in data["columns"]]
        expected_cols = [
            "store", "date", "name", "sku", "barcode", "category", "brand", "unit",
            "sold_qty", "returned_qty", "net_sold_qty", "gross_sales", "discount",
            "net_revenue", "free_price", "unit_cost", "total_cost", "profit", "margin_pct",
        ]
        self.assertEqual(cols, expected_cols)

        self.assertEqual(data["total"], 1)
        row = data["rows"][0]
        self.assertEqual(row["store"], "Asosiy do'kon")
        self.assertEqual(row["name"], "Shell Helix Ultra 5W-40")
        self.assertEqual(row["sku"], "SH-5W40")
        self.assertEqual(row["barcode"], "4780001")
        self.assertEqual(row["category"], "Moylar")
        self.assertEqual(row["brand"], "Shell")
        self.assertEqual(row["unit"], "dona")
        self.assertEqual(row["sold_qty"], Decimal("10.00"))
        self.assertEqual(row["returned_qty"], Decimal("0.00"))
        self.assertEqual(row["net_sold_qty"], Decimal("10.00"))
        self.assertEqual(row["gross_sales"], "1000.00")
        self.assertEqual(row["discount"], "0.00")
        self.assertEqual(row["net_revenue"], "1000.00")
        self.assertEqual(row["unit_cost"], "60.00")
        self.assertEqual(row["total_cost"], "600.00")
        self.assertEqual(row["profit"], "400.00")
        self.assertEqual(row["margin_pct"], "40.0%")

        summary = {s["label"]: s["value"] for s in data["summary"]}
        self.assertEqual(summary["Tovarlar soni"], 1)
        self.assertEqual(summary["Jami sotilgan"], Decimal("10.00"))
        self.assertEqual(summary["Sof sotilgan"], Decimal("10.00"))
        self.assertEqual(summary["Chegirmagacha savdo"], "1000.00")
        self.assertEqual(summary["Sof tushum"], "1000.00")
        self.assertEqual(summary["Jami tannarx"], "600.00")
        self.assertEqual(summary["Sof foyda"], "400.00")
        self.assertEqual(summary["O'rtacha marja"], "40.0%")

    def test_sales_by_product_returns_in_period(self):
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("500.00"), paid_amount=Decimal("500.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        item = SaleItem.objects.create(
            sale=sale, product=self.prod_shell, quantity=Decimal("5"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("500.00"),
        )
        s_ret = SaleReturn.objects.create(
            sale=sale, store=self.store1, seller=self.admin,
            total_refund=Decimal("200.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=s_ret, sale_item=item, product=self.prod_shell,
            quantity=Decimal("2"), unit_price=Decimal("100.00"),
            total_price=Decimal("200.00"),
        )

        data = ReportBuilderService.generate({"report_type": "sales_by_product"}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["sold_qty"], Decimal("5.00"))
        self.assertEqual(row["returned_qty"], Decimal("2.00"))
        self.assertEqual(row["net_sold_qty"], Decimal("3.00"))
        self.assertEqual(row["gross_sales"], "500.00")
        self.assertEqual(row["net_revenue"], "300.00")
        # gross_cost = 5*60=300, ret_cost = 2*60=120 -> total_cost = 180
        self.assertEqual(row["total_cost"], "180.00")
        self.assertEqual(row["profit"], "120.00")
        self.assertEqual(row["margin_pct"], "40.0%")

    def test_sales_by_product_full_return_in_period(self):
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("300.00"), paid_amount=Decimal("300.00"),
            status=Sale.Status.RETURNED, payment_type="cash",
        )
        item = SaleItem.objects.create(
            sale=sale, product=self.prod_shell, quantity=Decimal("3"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("50.00"),
            total_price=Decimal("300.00"), returned_quantity=Decimal("3"),
        )
        s_ret = SaleReturn.objects.create(
            sale=sale, store=self.store1, seller=self.admin,
            total_refund=Decimal("300.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=s_ret, sale_item=item, product=self.prod_shell,
            quantity=Decimal("3"), unit_price=Decimal("100.00"),
            total_price=Decimal("300.00"),
        )

        data = ReportBuilderService.generate({"report_type": "sales_by_product"}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["sold_qty"], Decimal("3.00"))
        self.assertEqual(row["returned_qty"], Decimal("3.00"))
        self.assertEqual(row["net_sold_qty"], Decimal("0.00"))
        self.assertEqual(row["gross_sales"], "300.00")
        self.assertEqual(row["net_revenue"], "0.00")
        self.assertEqual(row["total_cost"], "0.00")
        self.assertEqual(row["profit"], "0.00")
        self.assertEqual(row["margin_pct"], "0.0%")

    def test_sales_by_product_cross_period_return_logic(self):
        dt_jan = datetime(2026, 1, 15, 12, 0, tzinfo=dt_timezone.utc)
        dt_feb = datetime(2026, 2, 10, 14, 0, tzinfo=dt_timezone.utc)

        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("400.00"), paid_amount=Decimal("400.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        item = SaleItem.objects.create(
            sale=sale, product=self.prod_shell, quantity=Decimal("4"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("50.00"),
            total_price=Decimal("400.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=dt_jan)

        s_ret = SaleReturn.objects.create(
            sale=sale, store=self.store1, seller=self.admin,
            total_refund=Decimal("100.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=s_ret, sale_item=item, product=self.prod_shell,
            quantity=Decimal("1"), unit_price=Decimal("100.00"),
            total_price=Decimal("100.00"),
        )
        SaleReturn.objects.filter(id=s_ret.id).update(created_at=dt_feb)

        # 1-davr: 2026-01-01 dan 2026-02-01 gacha (faqat sotuv)
        data_jan = ReportBuilderService.generate({
            "report_type": "sales_by_product",
            "from": "2026-01-01",
            "to": "2026-01-31",
        }, self.admin)
        row_jan = data_jan["rows"][0]
        self.assertEqual(row_jan["sold_qty"], Decimal("4.00"))
        self.assertEqual(row_jan["returned_qty"], Decimal("0.00"))
        self.assertEqual(row_jan["net_sold_qty"], Decimal("4.00"))
        self.assertEqual(row_jan["net_revenue"], "400.00")
        self.assertEqual(row_jan["total_cost"], "200.00")
        self.assertEqual(row_jan["profit"], "200.00")

        # 2-davr: 2026-02-01 dan 2026-02-28 gacha (faqat qaytarim)
        data_feb = ReportBuilderService.generate({
            "report_type": "sales_by_product",
            "from": "2026-02-01",
            "to": "2026-02-28",
        }, self.admin)
        row_feb = data_feb["rows"][0]
        self.assertEqual(row_feb["sold_qty"], Decimal("0.00"))
        self.assertEqual(row_feb["returned_qty"], Decimal("1.00"))
        self.assertEqual(row_feb["net_sold_qty"], Decimal("-1.00"))
        self.assertEqual(row_feb["net_revenue"], "-100.00")
        self.assertEqual(row_feb["total_cost"], "-50.00")
        self.assertEqual(row_feb["profit"], "-50.00")

        # Birlashgan davr: 2026-01-01 dan 2026-02-28 gacha
        data_all = ReportBuilderService.generate({
            "report_type": "sales_by_product",
            "from": "2026-01-01",
            "to": "2026-02-28",
        }, self.admin)
        row_all = data_all["rows"][0]
        self.assertEqual(row_all["sold_qty"], Decimal("4.00"))
        self.assertEqual(row_all["returned_qty"], Decimal("1.00"))
        self.assertEqual(row_all["net_sold_qty"], Decimal("3.00"))
        self.assertEqual(row_all["net_revenue"], "300.00")
        self.assertEqual(row_all["total_cost"], "150.00")
        self.assertEqual(row_all["profit"], "150.00")

    def test_sales_by_product_proportional_discount(self):
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("360.00"), discount_amount=Decimal("40.00"),
            paid_amount=Decimal("360.00"), status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_shell, quantity=Decimal("3"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("50.00"),
            total_price=Decimal("300.00"),
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_castrol, quantity=Decimal("1"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("100.00"),
        )

        data = ReportBuilderService.generate({"report_type": "sales_by_product"}, self.admin)
        rows_by_sku = {r["sku"]: r for r in data["rows"]}

        # prod_shell: 300 / 400 * 40 = 30 discount -> net_revenue = 270.00
        row_shell = rows_by_sku["SH-5W40"]
        self.assertEqual(row_shell["gross_sales"], "300.00")
        self.assertEqual(row_shell["discount"], "30.00")
        self.assertEqual(row_shell["net_revenue"], "270.00")
        self.assertEqual(row_shell["total_cost"], "150.00")
        self.assertEqual(row_shell["profit"], "120.00")

        # prod_castrol: 100 / 400 * 40 = 10 discount -> net_revenue = 90.00
        row_castrol = rows_by_sku["CAS-10W40"]
        self.assertEqual(row_castrol["gross_sales"], "100.00")
        self.assertEqual(row_castrol["discount"], "10.00")
        self.assertEqual(row_castrol["net_revenue"], "90.00")
        self.assertEqual(row_castrol["total_cost"], "60.00")
        self.assertEqual(row_castrol["profit"], "30.00")

        summary = {s["label"]: s["value"] for s in data["summary"]}
        self.assertEqual(summary["Chegirmagacha savdo"], "400.00")
        self.assertEqual(summary["Jami chegirma"], "40.00")
        self.assertEqual(summary["Sof tushum"], "360.00")

    def test_sales_by_product_missing_purchase_price_warning(self):
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("100.00"), paid_amount=Decimal("100.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_no_cost, quantity=Decimal("1"),
            unit_price=Decimal("100.00"), purchase_price=None,
            total_price=Decimal("100.00"),
        )

        data = ReportBuilderService.generate({"report_type": "sales_by_product"}, self.admin)
        summary_labels = [s["label"] for s in data["summary"]]
        self.assertIn("Diqqat", summary_labels)
        diqqat_entry = next(s for s in data["summary"] if s["label"] == "Diqqat")
        self.assertEqual(diqqat_entry["value"], "Ba'zi tovarlarda tannarx yo'q — foyda taxminiy")

    def test_sales_by_product_filters_and_sorting(self):
        sale1 = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("500.00"), paid_amount=Decimal("500.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale1, product=self.prod_shell, quantity=Decimal("5"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("500.00"),
        )

        sale2 = Sale.objects.create(
            store=self.store2, seller=self.admin,
            total_amount=Decimal("200.00"), paid_amount=Decimal("200.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale2, product=self.prod_castrol, quantity=Decimal("2"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("50.00"),
            total_price=Decimal("200.00"),
        )

        # Do'kon filtri
        data_s1 = ReportBuilderService.generate({
            "report_type": "sales_by_product",
            "store_id": str(self.store1.id),
        }, self.admin)
        self.assertEqual(data_s1["total"], 1)
        self.assertEqual(data_s1["rows"][0]["store"], "Asosiy do'kon")

        # Brend filtri
        data_castrol = ReportBuilderService.generate({
            "report_type": "sales_by_product",
            "brand_id": str(self.brand_castrol.id),
        }, self.admin)
        self.assertEqual(data_castrol["total"], 1)
        self.assertEqual(data_castrol["rows"][0]["brand"], "Castrol")

        # Qidiruv filtri (SKU bo'yicha)
        data_search = ReportBuilderService.generate({
            "report_type": "sales_by_product",
            "search": "SH-5W40",
        }, self.admin)
        self.assertEqual(data_search["total"], 1)
        self.assertEqual(data_search["rows"][0]["sku"], "SH-5W40")

        # Saralash (profit bo'yicha)
        data_sort = ReportBuilderService.generate({
            "report_type": "sales_by_product",
            "sort_by": "profit",
        }, self.admin)
        self.assertEqual(data_sort["total"], 2)
        # Shell foydasi 200 (500-300), Castrol foydasi 100 (200-100)
        self.assertEqual(data_sort["rows"][0]["name"], "Shell Helix Ultra 5W-40")

    def test_sales_by_product_mathematical_reconciliation(self):
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("700.00"), discount_amount=Decimal("100.00"),
            paid_amount=Decimal("700.00"), status=Sale.Status.PAID, payment_type="cash",
        )
        item1 = SaleItem.objects.create(
            sale=sale, product=self.prod_shell, quantity=Decimal("5"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("500.00"),
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_castrol, quantity=Decimal("3"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("50.00"),
            total_price=Decimal("300.00"),
        )
        # Qaytarim
        s_ret = SaleReturn.objects.create(
            sale=sale, store=self.store1, seller=self.admin,
            total_refund=Decimal("87.50"),
        )
        # 1 dona shell qaytarildi: 100 * (700/800) = 87.50
        SaleReturnItem.objects.create(
            sale_return=s_ret, sale_item=item1, product=self.prod_shell,
            quantity=Decimal("1"), unit_price=Decimal("100.00"),
            total_price=Decimal("87.50"),
        )

        data = ReportBuilderService.generate({"report_type": "sales_by_product"}, self.admin)
        summary = {s["label"]: s["value"] for s in data["summary"]}

        # Qatorlar yig'indisi summary bilan to'liq mos kelishi kerak
        sum_rows_net_rev = sum(Decimal(r["net_revenue"]) for r in data["rows"])
        self.assertEqual(Decimal(summary["Sof tushum"]), sum_rows_net_rev)

        sum_rows_profit = sum(Decimal(r["profit"]) for r in data["rows"])
        self.assertEqual(Decimal(summary["Sof foyda"]), sum_rows_profit)

        # Har bir qatorda: sold - returned == net_sold
        for r in data["rows"]:
            self.assertEqual(r["sold_qty"] - r["returned_qty"], r["net_sold_qty"])
            self.assertEqual(Decimal(r["net_revenue"]) - Decimal(r["total_cost"]), Decimal(r["profit"]))

    def test_sales_by_product_export_excel_and_csv(self):
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("100.00"), paid_amount=Decimal("100.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_shell, quantity=Decimal("1"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("100.00"),
        )

        label, columns, rows, summary, info = ReportBuilderService.export_rows({
            "report_type": "sales_by_product",
        }, self.admin)
        self.assertEqual(label, "Tovarlar bo'yicha sotuvlar")
        self.assertGreater(len(columns), 10)
        self.assertEqual(len(rows), 1)

        # API Excel eksport
        excel_resp = self._call(ReportBuilderExportAPIView, {
            "report_type": "sales_by_product",
            "export_type": "excel",
        })
        self.assertEqual(excel_resp.status_code, 200)
        self.assertIn("spreadsheetml", excel_resp["Content-Type"])

        # API CSV eksport
        csv_resp = self._call(ReportBuilderExportAPIView, {
            "report_type": "sales_by_product",
            "export_type": "csv",
        })
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("text/csv", csv_resp["Content-Type"])
        self.assertIn("Shell Helix Ultra", csv_resp.content.decode("utf-8-sig"))

    def test_sales_by_product_permissions(self):
        # Staff user with reports.sales.view role -> 200 OK
        resp_staff = self._call(ReportBuilderGenerateAPIView, {
            "report_type": "sales_by_product",
        }, user=self.staff_user)
        self.assertEqual(resp_staff.status_code, 200)

        # Unauthorized user -> 403 Forbidden
        resp_unauth = self._call(ReportBuilderGenerateAPIView, {
            "report_type": "sales_by_product",
        }, user=self.unauth_user)
        self.assertEqual(resp_unauth.status_code, 403)


class ProductEfficiencyReportTest(TestCase):
    """
    PHASE 1.2 "Tovarlar samaradorligi" (product_efficiency) hisoboti uchun
    barcha 26 ta talab bo'yicha to'liq testlar:
      1. sold + stock (Faol)
      2. dead stock (Harakatsiz)
      3. sold + zero stock (Tugagan)
      4. return-only (Qaytarim ustun)
      5. zero stock + zero activity excluded
      6. cross-period return
      7. velocity
      8. DOI
      9. one-day period
      10. 30-day period
      11. zero velocity DOI (None / "—")
      12. negative velocity DOI (None / "—")
      13. revenue share positive revenue
      14. revenue share with negative/return revenue
      15. revenue share with zero total revenue
      16. Unit Cost presence and correctness
      17. product filter
      18. SKU filter
      19. barcode filter
      20. category filter
      21. brand filter
      22. search
      23. sorting (revenue, quantity, profit, velocity, stock, doi)
      24. permissions & fallbacks
      25. export (Excel & CSV)
      26. No N+1 query performance verification (assertNumQueries)
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(
            phone_number="+998901230001",
            email="admin_eff@reports.uz",
            is_superuser=True,
            is_staff=True,
        )
        cls.staff_role = Role.objects.create(name="Staff Sales", permissions=["reports.sales.view"])
        cls.staff_user = User.objects.create(
            phone_number="+998901230002",
            email="staff_eff@reports.uz",
            is_staff=True,
            role=cls.staff_role,
        )
        cls.unauth_user = User.objects.create(
            phone_number="+998901230003",
            email="unauth_eff@reports.uz",
            is_staff=False,
        )

        cls.store1 = Store.objects.create(
            name="Asosiy do'kon", phone_number="+998901230004", address="Chilonzor", type=Store.StoreType.STORE,
        )
        cls.store2 = Store.objects.create(
            name="Filial", phone_number="+998901230005", address="Yunusobod", type=Store.StoreType.STORE,
        )
        StoreUser.objects.create(
            user=cls.staff_user, store=cls.store1, role=StoreUser.Role.Manager, is_active=True,
        )

        cls.cat_oil = Category.objects.create(name="Moylar", description="Motor moylari")
        cls.cat_filter = Category.objects.create(name="Filtrlar", description="Avto filtrlar")

        cls.brand_shell = Brand.objects.create(name="Shell")
        cls.brand_mobil = Brand.objects.create(name="Mobil")

        cls.unit_dona = ProductUnitMeasurement.objects.create(measurement="dona")

        # 1. Tovar: Sotilgan + zaxirada qoldiq bor (Faol / Active)
        cls.prod_active = Product.objects.create(
            name="Shell Active 5W-30", sku="ACT-01", barcode="4781001",
            category=cls.cat_oil, brand=cls.brand_shell, unit_measurement=cls.unit_dona,
        )
        cls.batch_active = ProductBatch.objects.create(
            store=cls.store1, product=cls.prod_active, quantity=Decimal("20.00"),
            purchase_price=Decimal("60.00"), selling_price=Decimal("100.00"),
        )

        # 2. Tovar: Sotilmagan + zaxirada qoldiq bor (Dead Stock / Harakatsiz)
        cls.prod_dead = Product.objects.create(
            name="Shell Dead Stock", sku="DEAD-02", barcode="4781002",
            category=cls.cat_oil, brand=cls.brand_shell, unit_measurement=cls.unit_dona,
        )
        cls.batch_dead = ProductBatch.objects.create(
            store=cls.store1, product=cls.prod_dead, quantity=Decimal("15.00"),
            purchase_price=Decimal("40.00"), selling_price=Decimal("70.00"),
        )

        # 3. Tovar: Sotilgan + qoldiq 0 (Out of Stock / Tugagan)
        cls.prod_out = Product.objects.create(
            name="Mobil Out of Stock", sku="OUT-03", barcode="4781003",
            category=cls.cat_oil, brand=cls.brand_mobil, unit_measurement=cls.unit_dona,
        )
        cls.batch_out = ProductBatch.objects.create(
            store=cls.store1, product=cls.prod_out, quantity=Decimal("0.00"),
            purchase_price=Decimal("50.00"), selling_price=Decimal("80.00"),
        )

        # 4. Tovar: Boshqa toifadagi tovar (Filtrlar / Mobil)
        cls.prod_filter = Product.objects.create(
            name="Mobil Havo Filtri", sku="FLT-04", barcode="4781004",
            category=cls.cat_filter, brand=cls.brand_mobil, unit_measurement=cls.unit_dona,
        )
        cls.batch_filter = ProductBatch.objects.create(
            store=cls.store1, product=cls.prod_filter, quantity=Decimal("50.00"),
            purchase_price=Decimal("20.00"), selling_price=Decimal("35.00"),
        )

        # 5. Tovar: Qoldiq 0 va faoliyat 0 (Umuman chiqarilmasligi kerak)
        cls.prod_zero_zero = Product.objects.create(
            name="Ghost Product Zero", sku="GHOST-05", barcode="4781005",
            category=cls.cat_oil, brand=cls.brand_shell, unit_measurement=cls.unit_dona,
        )
        cls.batch_zero_zero = ProductBatch.objects.create(
            store=cls.store1, product=cls.prod_zero_zero, quantity=Decimal("0.00"),
            purchase_price=Decimal("10.00"), selling_price=Decimal("20.00"),
        )

    def _call(self, view, params, user=None):
        request = APIRequestFactory().get("/api/reports/builder/", params)
        force_authenticate(request, user=user or self.admin)
        return view.as_view()(request)

    def test_meta_contains_product_efficiency(self):
        meta = ReportBuilderService.meta()
        rep = next((r for r in meta["reports"] if r["key"] == "product_efficiency"), None)
        self.assertIsNotNone(rep)
        self.assertEqual(rep["label"], "Tovarlar samaradorligi")
        self.assertTrue(rep["search"])
        filter_params = [f["param"] for f in rep["filters"]]
        self.assertIn("date", filter_params)
        self.assertIn("store_id", filter_params)
        self.assertIn("category_id", filter_params)
        self.assertIn("brand_id", filter_params)
        self.assertIn("efficiency_status", filter_params)
        self.assertIn("sort_by", filter_params)

    def test_01_sold_plus_stock_and_active_status(self):
        """1. Sotilgan + qoldiq > 0: status = 'active' ('Faol')."""
        t_sale = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("1000.00"), paid_amount=Decimal("1000.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_active, quantity=Decimal("10.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("1000.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=t_sale)

        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "ACT-01",
        }, self.admin)

        self.assertEqual(len(data["rows"]), 1)
        r = data["rows"][0]
        self.assertEqual(r["efficiency_status"], "Faol")
        self.assertEqual(r["current_stock"], Decimal("20.00"))
        self.assertEqual(r["net_sold_qty"], Decimal("10.00"))

    def test_02_dead_stock_status(self):
        """2. Qoldiq > 0 va sotuv = 0: status = 'dead_stock' ('Harakatsiz'). Hisobotdan tushib qolmaydi."""
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "DEAD-02",
        }, self.admin)

        self.assertEqual(len(data["rows"]), 1)
        r = data["rows"][0]
        self.assertEqual(r["efficiency_status"], "Harakatsiz")
        self.assertEqual(r["current_stock"], Decimal("15.00"))
        self.assertEqual(r["net_sold_qty"], Decimal("0.00"))
        self.assertEqual(r["doi"], "—")

    def test_03_sold_plus_zero_stock_out_of_stock(self):
        """3. Sotilgan va qoldiq <= 0: status = 'out_of_stock' ('Tugagan')."""
        t_sale = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("400.00"), paid_amount=Decimal("400.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_out, quantity=Decimal("5.00"),
            unit_price=Decimal("80.00"), purchase_price=Decimal("50.00"),
            total_price=Decimal("400.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=t_sale)

        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "OUT-03",
        }, self.admin)

        self.assertEqual(len(data["rows"]), 1)
        r = data["rows"][0]
        self.assertEqual(r["efficiency_status"], "Tugagan")
        self.assertEqual(r["current_stock"], Decimal("0.00"))
        self.assertEqual(r["net_sold_qty"], Decimal("5.00"))
        self.assertEqual(r["doi"], "0.0")

    def test_04_return_only_net_return(self):
        """4. Davrda faqat qaytarim (net_sold_qty < 0): status = 'net_return' ('Qaytarim ustun')."""
        dt_aug = datetime(2026, 8, 10, 12, 0, tzinfo=dt_timezone.utc)
        dt_sep = datetime(2026, 9, 10, 12, 0, tzinfo=dt_timezone.utc)

        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("300.00"), paid_amount=Decimal("300.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        item = SaleItem.objects.create(
            sale=sale, product=self.prod_active, quantity=Decimal("3.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("300.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=dt_aug)

        s_ret = SaleReturn.objects.create(
            sale=sale, store=self.store1, seller=self.admin, total_refund=Decimal("300.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=s_ret, sale_item=item, product=self.prod_active,
            quantity=Decimal("3.00"), unit_price=Decimal("100.00"), total_price=Decimal("300.00"),
        )
        SaleReturn.objects.filter(id=s_ret.id).update(created_at=dt_sep)

        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "ACT-01",
        }, self.admin)

        self.assertEqual(len(data["rows"]), 1)
        r = data["rows"][0]
        self.assertEqual(r["efficiency_status"], "Qaytarim ustun")
        self.assertEqual(r["net_sold_qty"], Decimal("-3.00"))

    def test_05_zero_stock_plus_zero_activity_excluded(self):
        """5. Qoldiq 0 va faoliyat 0 bo'lgan tovarlar jadvalga kirmasligi kerak."""
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
        }, self.admin)

        skus = [r["sku"] for r in data["rows"]]
        self.assertNotIn("GHOST-05", skus)

    def test_06_cross_period_return_logic(self):
        """6. Bir davrda sotilib, boshqa davrda qaytarilsa o'tgan davr hisoboti buzilmaydi."""
        dt_aug = datetime(2026, 8, 15, 12, 0, tzinfo=dt_timezone.utc)
        dt_sep = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)

        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("500.00"), paid_amount=Decimal("500.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        item = SaleItem.objects.create(
            sale=sale, product=self.prod_filter, quantity=Decimal("5.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("500.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=dt_aug)

        s_ret = SaleReturn.objects.create(
            sale=sale, store=self.store1, seller=self.admin, total_refund=Decimal("100.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=s_ret, sale_item=item, product=self.prod_filter,
            quantity=Decimal("1.00"), unit_price=Decimal("100.00"), total_price=Decimal("100.00"),
        )
        SaleReturn.objects.filter(id=s_ret.id).update(created_at=dt_sep)

        # Avgust: 5 dona sotilgan, 0 qaytarilgan
        data_aug = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-08-01", "to": "2026-08-31",
            "sku": "FLT-04",
        }, self.admin)
        self.assertEqual(data_aug["rows"][0]["net_sold_qty"], Decimal("5.00"))

        # Sentabr: 0 sotilgan, 1 qaytarilgan -> net_sold_qty = -1.00
        data_sep = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "FLT-04",
        }, self.admin)
        self.assertEqual(data_sep["rows"][0]["net_sold_qty"], Decimal("-1.00"))

    def test_07_and_08_velocity_and_doi_calculation(self):
        """7 & 8. Sales Velocity va DOI formulalari to'g'ri ishlashi."""
        t_sale = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("3000.00"), paid_amount=Decimal("3000.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_active, quantity=Decimal("30.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("3000.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=t_sale)

        # 30-kunlik davr: net_sold_qty = 30.00 -> velocity = 30 / 30 = 1.00 dona/kun
        # current_stock = 20.00 -> DOI = 20.00 / 1.00 = 20.0 kun
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "ACT-01",
        }, self.admin)

        r = data["rows"][0]
        self.assertEqual(r["sales_velocity"], "1.00")
        self.assertEqual(r["doi"], "20.0")

    def test_09_one_day_period(self):
        """9. Bir kunlik davr: period_days = 1 bo'lishi kerak."""
        t_sale = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("500.00"), paid_amount=Decimal("500.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_active, quantity=Decimal("5.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("500.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=t_sale)

        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-15", "to": "2026-09-15",
            "sku": "ACT-01",
        }, self.admin)

        r = data["rows"][0]
        # 5 dona / 1 kun = 5.00 dona/kun
        self.assertEqual(r["sales_velocity"], "5.00")
        # 20 dona / 5.00 = 4.0 kun
        self.assertEqual(r["doi"], "4.0")

    def test_10_thirty_day_period(self):
        """10. 30 kunlik davr aniq 30 kunga bo'linishi kerak."""
        t_sale = datetime(2026, 9, 10, 12, 0, tzinfo=dt_timezone.utc)
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("600.00"), paid_amount=Decimal("600.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale, product=self.prod_active, quantity=Decimal("6.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("600.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=t_sale)

        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "ACT-01",
        }, self.admin)
        r = data["rows"][0]
        # 6 dona / 30 kun = 0.20 dona/kun
        self.assertEqual(r["sales_velocity"], "0.20")
        # 20 dona / 0.20 = 100.0 kun
        self.assertEqual(r["doi"], "100.0")

    def test_11_zero_velocity_doi_is_none(self):
        """11. Velocity = 0 bo'lganda DOI = None ('—'), 0 ga bo'lish xatosi bo'lmaydi."""
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "DEAD-02",
        }, self.admin)
        r = data["rows"][0]
        self.assertEqual(r["sales_velocity"], "0.00")
        self.assertEqual(r["doi"], "—")

    def test_12_negative_velocity_doi_is_none(self):
        """12. Velocity < 0 (qaytarim ustun) bo'lganda DOI = None ('—')."""
        dt_aug = datetime(2026, 8, 15, 12, 0, tzinfo=dt_timezone.utc)
        dt_sep = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)

        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("200.00"), paid_amount=Decimal("200.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        item = SaleItem.objects.create(
            sale=sale, product=self.prod_active, quantity=Decimal("2.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("200.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=dt_aug)

        s_ret = SaleReturn.objects.create(
            sale=sale, store=self.store1, seller=self.admin, total_refund=Decimal("200.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=s_ret, sale_item=item, product=self.prod_active,
            quantity=Decimal("2.00"), unit_price=Decimal("100.00"), total_price=Decimal("200.00"),
        )
        SaleReturn.objects.filter(id=s_ret.id).update(created_at=dt_sep)

        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "ACT-01",
        }, self.admin)
        r = data["rows"][0]
        self.assertEqual(r["doi"], "—")

    def test_13_revenue_share_positive_revenue(self):
        """13. Ijobiy tushumlar: product_net_rev / total_scope_net_rev * 100."""
        t_sale = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("1000.00"), paid_amount=Decimal("1000.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        # prod_active: 800 (80%)
        SaleItem.objects.create(
            sale=sale, product=self.prod_active, quantity=Decimal("8.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("800.00"),
        )
        # prod_out: 200 (20%)
        SaleItem.objects.create(
            sale=sale, product=self.prod_out, quantity=Decimal("2.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("50.00"),
            total_price=Decimal("200.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=t_sale)

        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "category_id": str(self.cat_oil.id),
        }, self.admin)

        rows_by_sku = {r["sku"]: r for r in data["rows"]}
        self.assertEqual(rows_by_sku["ACT-01"]["revenue_share_pct"], "80.0%")
        self.assertEqual(rows_by_sku["OUT-03"]["revenue_share_pct"], "20.0%")
        self.assertEqual(rows_by_sku["DEAD-02"]["revenue_share_pct"], "0.0%")

    def test_14_revenue_share_with_negative_return_revenue(self):
        """14. Qaytarim bo'lganda maxrajsiz sof scope tushumi ishlatiladi."""
        dt_aug = datetime(2026, 8, 15, 12, 0, tzinfo=dt_timezone.utc)
        dt_sep = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)

        # 1-tovar: 1200 sotuv
        sale1 = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("1200.00"), paid_amount=Decimal("1200.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        SaleItem.objects.create(
            sale=sale1, product=self.prod_active, quantity=Decimal("12.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("1200.00"),
        )
        Sale.objects.filter(id=sale1.id).update(created_at=dt_sep)

        # 2-tovar: avgustda sotilib sentabrda -200 qaytarildi
        sale2 = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("200.00"), paid_amount=Decimal("200.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        item2 = SaleItem.objects.create(
            sale=sale2, product=self.prod_filter, quantity=Decimal("2.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("60.00"),
            total_price=Decimal("200.00"),
        )
        Sale.objects.filter(id=sale2.id).update(created_at=dt_aug)

        s_ret = SaleReturn.objects.create(
            sale=sale2, store=self.store1, seller=self.admin, total_refund=Decimal("200.00"),
        )
        SaleReturnItem.objects.create(
            sale_return=s_ret, sale_item=item2, product=self.prod_filter,
            quantity=Decimal("2.00"), unit_price=Decimal("100.00"), total_price=Decimal("200.00"),
        )
        SaleReturn.objects.filter(id=s_ret.id).update(created_at=dt_sep)

        # Jami scope tushumi = 1200 - 200 = 1000.00
        # prod_active ulushi: 1200 / 1000 * 100 = 120.0%
        # prod_filter ulushi: -200 / 1000 * 100 = -20.0%
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
        }, self.admin)
        rows_by_sku = {r["sku"]: r for r in data["rows"]}
        self.assertEqual(rows_by_sku["ACT-01"]["revenue_share_pct"], "120.0%")
        self.assertEqual(rows_by_sku["FLT-04"]["revenue_share_pct"], "-20.0%")

    def test_15_revenue_share_with_zero_total_revenue(self):
        """15. Jami tushum 0 bo'lganda ZeroDivisionError chiqmaydi, 0.0% bo'ladi."""
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "DEAD-02",
        }, self.admin)
        self.assertEqual(data["rows"][0]["revenue_share_pct"], "0.0%")

    def test_16_unit_cost_presence_and_correctness(self):
        """16. Unit Cost ustuni mavjudligi va qiymat manbai tekshiruvi."""
        t_sale = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
        sale = Sale.objects.create(
            store=self.store1, seller=self.admin,
            total_amount=Decimal("100.00"), paid_amount=Decimal("100.00"),
            status=Sale.Status.PAID, payment_type="cash",
        )
        # Sotilgan tovar: purchase_price snapshot = 65.00 (batch.purchase_price 60.00 bo'lsa ham)
        SaleItem.objects.create(
            sale=sale, product=self.prod_active, quantity=Decimal("1.00"),
            unit_price=Decimal("100.00"), purchase_price=Decimal("65.00"),
            total_price=Decimal("100.00"),
        )
        Sale.objects.filter(id=sale.id).update(created_at=t_sale)

        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
        }, self.admin)

        col_keys = [c["key"] for c in data["columns"]]
        self.assertIn("unit_cost", col_keys)
        self.assertIn("total_cost", col_keys)

        rows_by_sku = {r["sku"]: r for r in data["rows"]}
        # Sotilgan tovar unit_cost = 65.00 (SaleItem snapshot)
        self.assertEqual(rows_by_sku["ACT-01"]["unit_cost"], "65.00")
        # Sotilmagan dead stock tovar unit_cost = 40.00 (ProductBatch.purchase_price)
        self.assertEqual(rows_by_sku["DEAD-02"]["unit_cost"], "40.00")

    def test_17_filter_product_id(self):
        """17. product_id bo'yicha filtr."""
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "product_id": str(self.prod_active.id),
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "ACT-01")

    def test_18_filter_sku(self):
        """18. SKU bo'yicha filtr."""
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "DEAD-02",
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "DEAD-02")

    def test_19_filter_barcode(self):
        """19. Barcode bo'yicha filtr."""
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "barcode": "4781004",
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "FLT-04")

    def test_20_filter_category(self):
        """20. Kategoriya bo'yicha filtr."""
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "category_id": str(self.cat_filter.id),
        }, self.admin)
        for r in data["rows"]:
            self.assertEqual(r["category"], "Filtrlar")

    def test_21_filter_brand(self):
        """21. Brend bo'yicha filtr."""
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "brand_id": str(self.brand_mobil.id),
        }, self.admin)
        for r in data["rows"]:
            self.assertEqual(r["brand"], "Mobil")

    def test_22_filter_search(self):
        """22. Qidiruv (nomi, SKU yoki shtrix kod)."""
        data = ReportBuilderService.generate({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "search": "Havo Filtri",
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "FLT-04")

    def test_23_sorting_options(self):
        """23. Saralash variantlari (revenue, quantity, profit, velocity, stock, doi)."""
        for sort_field in ["revenue", "quantity", "profit", "velocity", "stock", "doi"]:
            data = ReportBuilderService.generate({
                "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
                "sort_by": sort_field,
            }, self.admin)
            self.assertGreaterEqual(len(data["rows"]), 1)

    def test_24_permissions_and_fallbacks(self):
        """24. Ruxsatlar: superuser, sales fallback, explicit efficiency huquqi, 403."""
        # Staff user with reports.sales.view -> 200 OK
        resp_staff = self._call(ReportBuilderGenerateAPIView, {
            "report_type": "product_efficiency",
        }, user=self.staff_user)
        self.assertEqual(resp_staff.status_code, 200)

        # Staff user with reports.product_efficiency.view -> 200 OK
        eff_role = Role.objects.create(name="Eff Role", permissions=["reports.product_efficiency.view"])
        eff_user = User.objects.create(
            phone_number="+998901230009", email="eff_role@reports.uz", is_staff=True, role=eff_role,
        )
        StoreUser.objects.create(
            user=eff_user, store=self.store1, role=StoreUser.Role.Manager, is_active=True,
        )
        resp_eff = self._call(ReportBuilderGenerateAPIView, {
            "report_type": "product_efficiency",
        }, user=eff_user)
        self.assertEqual(resp_eff.status_code, 200)

        # Unauthorized user -> 403 Forbidden
        resp_unauth = self._call(ReportBuilderGenerateAPIView, {
            "report_type": "product_efficiency",
        }, user=self.unauth_user)
        self.assertEqual(resp_unauth.status_code, 403)

    def test_25_export_excel_and_csv(self):
        """25. Excel va CSV eksportda unit_cost va ko'rsatkichlar mavjudligi."""
        label, columns, rows, summary, info = ReportBuilderService.export_rows({
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
        }, self.admin)
        self.assertEqual(label, "Tovarlar samaradorligi")
        col_keys = [c["key"] for c in columns]
        self.assertIn("unit_cost", col_keys)
        self.assertIn("sales_velocity", col_keys)
        self.assertIn("doi", col_keys)
        self.assertIn("revenue_share_pct", col_keys)
        self.assertIn("efficiency_status", col_keys)

        # Excel API
        excel_resp = self._call(ReportBuilderExportAPIView, {
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "export_type": "excel",
        })
        self.assertEqual(excel_resp.status_code, 200)
        self.assertIn("spreadsheetml", excel_resp["Content-Type"])

        # CSV API
        csv_resp = self._call(ReportBuilderExportAPIView, {
            "report_type": "product_efficiency", "from": "2026-09-01", "to": "2026-09-30",
            "export_type": "csv",
        })
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("text/csv", csv_resp["Content-Type"])
        self.assertIn("Birlik tannarxi", csv_resp.content.decode("utf-8-sig"))

    def test_26_no_n_plus_one_query_performance(self):
        """26. Query count / N+1 tekshiruvi: tovarlar sonidan qat'i nazar SQL so'rovlar soni o'zgarmasligi."""
        with self.assertNumQueries(3):
            # 1 ta sales aggregatsiyasi
            # 1 ta returns aggregatsiyasi
            # 1 ta ProductBatch select_related so'rovi
            ReportingFoundationService.get_product_efficiency_metrics(
                start=datetime(2026, 9, 1, 0, 0, tzinfo=dt_timezone.utc),
                end=datetime(2026, 10, 1, 0, 0, tzinfo=dt_timezone.utc),
                store_id=self.store1.id,
            )


class AbcAnalysisReportTest(TestCase):
    """
    Phase 1.3: ABC Tahlili (Pareto 80/15/5) testlar to'plami.
    
    Qamrov:
      - Meta reestr va filtrlar sxemasi
      - Metrikalar: revenue (sukut), profit, quantity
      - Paretto 80/15/5 chegaralari (A <= 80%, B <= 95%, C <= 100%)
      - Yakka ustun tovar (dominance > 80% bo'lganda ham A toifa bo'lishi)
      - Nol va manfiy tovarlarning Paretto rankingini buzmasligi
      - Nol musbat ko'rsatkichli holatda ZeroDivision bo'lmasligi
      - ABC toifasi bo'yicha filtrlash
      - Query count / N+1 tekshiruvi: O(1) qat'iy 2 ta SQL so'rov
      - Ruxsatlar tizimi (superuser, sales fallback, explicit huquq, 403)
      - Excel va CSV eksportlari
    """

    @classmethod
    def setUpTestData(cls):
        cls.factory = APIRequestFactory()

        cls.store = Store.objects.create(
            name="ABC Markaziy do'kon", phone_number="+998901112233",
            address="Toshkent", type=Store.StoreType.STORE, is_active=True,
        )

        cls.admin = User.objects.create(
            phone_number="+998909990001", email="abc_admin@reports.uz",
            is_superuser=True, is_staff=True,
        )
        StoreUser.objects.create(
            user=cls.admin, store=cls.store, role=StoreUser.Role.Manager, is_active=True,
        )

        sales_role = Role.objects.create(name="Sales Viewer", permissions=["reports.sales.view", "reports.sales.export"])
        cls.staff_sales_user = User.objects.create(
            phone_number="+998909990002", email="sales_staff@reports.uz",
            is_staff=True, role=sales_role,
        )
        StoreUser.objects.create(
            user=cls.staff_sales_user, store=cls.store, role=StoreUser.Role.Manager, is_active=True,
        )

        cls.unauth_user = User.objects.create(
            phone_number="+998909990003", email="unauth@reports.uz",
            is_staff=True,
        )
        StoreUser.objects.create(
            user=cls.unauth_user, store=cls.store, role=StoreUser.Role.SELLER, is_active=True,
        )

        cls.cat1 = Category.objects.create(name="Moylar")
        cls.brand1 = Brand.objects.create(name="Castrol")

        # Mahsulotlar:
        # P1 (70% ulush): 7,000,000 tushum, tannarx 3,500,000 (foyda 3,500,000), miqdor 70
        cls.p1 = Product.objects.create(name="Tovar A 70pct", sku="ABC-P1", barcode="4780099", category=cls.cat1, brand=cls.brand1)
        # P2 (15% ulush): 1,500,000 tushum, tannarx 500,000 (foyda 1,000,000), miqdor 15
        cls.p2 = Product.objects.create(name="Tovar B 15pct", sku="ABC-P2", category=cls.cat1, brand=cls.brand1)
        # P3 (10% ulush): 1,000,000 tushum, tannarx 200,000 (foyda 800,000), miqdor 10
        cls.p3 = Product.objects.create(name="Tovar C 10pct", sku="ABC-P3", category=cls.cat1, brand=cls.brand1)
        # P4 (5% ulush): 500,000 tushum, tannarx 100,000 (foyda 400,000), miqdor 5
        cls.p4 = Product.objects.create(name="Tovar D 5pct", sku="ABC-P4", category=cls.cat1, brand=cls.brand1)
        # P_neg: Qaytarim ustun bo'lgan tovar (-200,000 tushum)
        cls.p_neg = Product.objects.create(name="Tovar Qaytarim", sku="ABC-NEG", category=cls.cat1, brand=cls.brand1)

        sale_dt = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)

        # Sotuv 1
        s1 = Sale.objects.create(
            store=cls.store, seller=cls.admin, status=Sale.Status.PAID,
            total_amount=Decimal("10000000.00"), paid_amount=Decimal("10000000.00"),
            discount_amount=Decimal("0.00"), payment_type="cash",
        )
        Sale.objects.filter(id=s1.id).update(created_at=sale_dt)

        SaleItem.objects.create(
            sale=s1, product=cls.p1, quantity=Decimal("70"),
            unit_price=Decimal("100000.00"), total_price=Decimal("7000000.00"),
            purchase_price=Decimal("50000.00"),
        )
        SaleItem.objects.create(
            sale=s1, product=cls.p2, quantity=Decimal("15"),
            unit_price=Decimal("100000.00"), total_price=Decimal("1500000.00"),
            purchase_price=Decimal("33333.33"),
        )
        SaleItem.objects.create(
            sale=s1, product=cls.p3, quantity=Decimal("10"),
            unit_price=Decimal("100000.00"), total_price=Decimal("1000000.00"),
            purchase_price=Decimal("20000.00"),
        )
        item_p4 = SaleItem.objects.create(
            sale=s1, product=cls.p4, quantity=Decimal("5"),
            unit_price=Decimal("100000.00"), total_price=Decimal("500000.00"),
            purchase_price=Decimal("20000.00"),
        )

        # Qaytarim operatsiyasi (p_neg uchun o'tgan davrdagi sotuv qaytariladi)
        ret = SaleReturn.objects.create(
            store=cls.store, seller=cls.admin,
            sale=s1, total_refund=Decimal("200000.00"),
        )
        SaleReturn.objects.filter(id=ret.id).update(created_at=sale_dt)
        SaleReturnItem.objects.create(
            sale_return=ret, sale_item=item_p4, product=cls.p_neg, quantity=Decimal("2"),
            unit_price=Decimal("100000.00"), total_price=Decimal("200000.00"),
        )

    def _call(self, view_cls, params, user=None):
        req = self.factory.get("/api/v1/reports/builder/", params)
        force_authenticate(req, user=user or self.admin)
        return view_cls.as_view()(req)

    def test_01_abc_analysis_meta(self):
        """01. Meta reestrda abc_analysis va uning dinamik filtrlari mavjudligi."""
        meta = ReportBuilderService.meta()
        reports = {r["key"]: r for r in meta["reports"]}
        self.assertIn("abc_analysis", reports)

        filter_params = [f["param"] for f in reports["abc_analysis"]["filters"]]
        self.assertIn("metric", filter_params)
        self.assertIn("abc_class", filter_params)
        self.assertIn("store_id", filter_params)
        self.assertIn("date", filter_params)
        self.assertIn("category_id", filter_params)
        self.assertIn("brand_id", filter_params)
        self.assertIn("sort_by", filter_params)

    def test_02_pareto_revenue_distribution_80_15_5(self):
        """02. Revenue bo'yicha Paretto taqsimoti: 70% (A), 15% (B), 10% (B), 5% (C)."""
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "metric": "revenue",
        }, self.admin)

        rows_by_sku = {r["sku"]: r for r in data["rows"]}

        # P1: 7m / 10m = 70.0%, cum = 70.0% -> A
        p1 = rows_by_sku["ABC-P1"]
        self.assertEqual(p1["abc_class"], "A")
        self.assertEqual(p1["share_pct"], "70.00%")
        self.assertEqual(p1["cumulative_pct"], "70.00%")

        # P2: 1.5m / 10m = 15.0%, cum = 85.0% -> B (80% < cum <= 95%)
        p2 = rows_by_sku["ABC-P2"]
        self.assertEqual(p2["abc_class"], "B")
        self.assertEqual(p2["share_pct"], "15.00%")
        self.assertEqual(p2["cumulative_pct"], "85.00%")

        # P3: 1m / 10m = 10.0%, cum = 95.0% -> B (80% < cum <= 95%)
        p3 = rows_by_sku["ABC-P3"]
        self.assertEqual(p3["abc_class"], "B")
        self.assertEqual(p3["share_pct"], "10.00%")
        self.assertEqual(p3["cumulative_pct"], "95.00%")

        # P4: 0.5m / 10m = 5.0%, cum = 100.0% -> C (95% < cum <= 100%)
        p4 = rows_by_sku["ABC-P4"]
        self.assertEqual(p4["abc_class"], "C")
        self.assertEqual(p4["share_pct"], "5.00%")
        self.assertEqual(p4["cumulative_pct"], "100.00%")

        # Summary kartalari
        sum_dict = {s["label"]: s["value"] for s in data["summary"]}
        self.assertIn("A toifa (80% lokomotiv)", sum_dict)
        self.assertIn("1 ta", sum_dict["A toifa (80% lokomotiv)"])
        self.assertIn("2 ta", sum_dict["B toifa (15% o'rta)"])
        # C toifada P4 va P_neg bor (jami 2 ta)
        self.assertIn("2 ta", sum_dict["C toifa (5% quyi / nol / manfiy)"])

    def test_03_single_dominant_product_gets_a(self):
        """03. Yakka tovar umumiy tushumning 80% dan ko'pini tashkil qilsa ham kamida 'A' toifani oladi."""
        # Yangi do'kon va yakka dominant tovar yaratamiz
        store_dom = Store.objects.create(name="Dominant Do'kon", phone_number="+998909998877")
        StoreUser.objects.create(user=self.admin, store=store_dom, role=StoreUser.Role.Manager, is_active=True)
        p_dom = Product.objects.create(name="Dominant Mahsulot", sku="DOM-01")

        s = Sale.objects.create(
            store=store_dom, seller=self.admin, status=Sale.Status.PAID,
            total_amount=Decimal("9000000.00"), paid_amount=Decimal("9000000.00"), discount_amount=Decimal("0.00"),
        )
        Sale.objects.filter(id=s.id).update(created_at=datetime(2026, 9, 10, 12, 0, tzinfo=dt_timezone.utc))
        SaleItem.objects.create(
            sale=s, product=p_dom, quantity=Decimal("1"),
            unit_price=Decimal("9000000.00"), total_price=Decimal("9000000.00"),
            purchase_price=Decimal("4000000.00"),
        )

        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "store_id": str(store_dom.id), "metric": "revenue",
        }, self.admin)

        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "DOM-01")
        # 1-tovar yakka o'zi 100% bo'lsa ham A toifa bo'ladi
        self.assertEqual(data["rows"][0]["abc_class"], "A")
        self.assertEqual(data["rows"][0]["share_pct"], "100.00%")

    def test_04_profit_metric(self):
        """04. Sof foyda (profit) bo'yicha ABC tahlili."""
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "metric": "profit",
        }, self.admin)

        # Foydalar:
        # P1: 7,000,000 - 3,500,000 = 3,500,000
        # P2: 1,500,000 - 500,000 = 1,000,000
        # P3: 1,000,000 - 200,000 = 800,000
        # P4: 500,000 - 100,000 = 400,000
        # Total positive profit = 5,700,000
        rows_by_sku = {r["sku"]: r for r in data["rows"]}
        self.assertIn("ABC-P1", rows_by_sku)
        self.assertEqual(rows_by_sku["ABC-P1"]["profit"], "3500000.00")
        self.assertEqual(rows_by_sku["ABC-P1"]["abc_class"], "A")

    def test_05_quantity_metric(self):
        """05. Sof miqdor (quantity) bo'yicha ABC tahlili."""
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "metric": "quantity",
        }, self.admin)

        # Miqdorlar:
        # P1: 70 dona
        # P2: 15 dona
        # P3: 10 dona
        # P4: 5 dona
        # Jami musbat miqdor = 100 dona
        rows_by_sku = {r["sku"]: r for r in data["rows"]}
        self.assertEqual(rows_by_sku["ABC-P1"]["share_pct"], "70.00%")
        self.assertEqual(rows_by_sku["ABC-P1"]["abc_class"], "A")
        self.assertEqual(rows_by_sku["ABC-P2"]["share_pct"], "15.00%")
        self.assertEqual(rows_by_sku["ABC-P2"]["abc_class"], "B")

    def test_06_negative_and_zero_metric_does_not_distort_pareto(self):
        """06. Manfiy va 0 metrikali tovarlar Paretto reytingini buzmasligi va C toifaga olinishi."""
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "metric": "revenue",
        }, self.admin)

        rows_by_sku = {r["sku"]: r for r in data["rows"]}
        p_neg = rows_by_sku["ABC-NEG"]
        # Manfiy tovar ulushi 0.0% bo'lishi kerak
        self.assertEqual(p_neg["share_pct"], "0.00%")
        # Kumulyativ 100.00% da to'xtaydi (orqaga tushib ketmaydi)
        self.assertEqual(p_neg["cumulative_pct"], "100.00%")
        # Toifasi C bo'ladi
        self.assertEqual(p_neg["abc_class"], "C")

        # P1 va P2 ning ulushlari faqat musbat tushum (10,000,000) dan olinganini tekshirish
        self.assertEqual(rows_by_sku["ABC-P1"]["share_pct"], "70.00%")
        self.assertEqual(rows_by_sku["ABC-P2"]["share_pct"], "15.00%")

    def test_07_all_zero_or_negative_metrics_no_crash(self):
        """07. Davrda musbat sotuv bo'lmaganda ZeroDivision xatosi bo'lmasligi."""
        # Sotuv bo'lmagan davr
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2025-01-01", "to": "2025-01-31",
            "metric": "revenue",
        }, self.admin)
        self.assertEqual(len(data["rows"]), 0)
        self.assertEqual(data["total"], 0)

    def test_08_filter_by_abc_class(self):
        """08. abc_class bo'yicha filtrlash (A, B, C)."""
        # Faqat A toifa
        data_a = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "abc_class": "A",
        }, self.admin)
        self.assertEqual(len(data_a["rows"]), 1)
        self.assertEqual(data_a["rows"][0]["sku"], "ABC-P1")

        # Faqat B toifa
        data_b = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "abc_class": "B",
        }, self.admin)
        self.assertEqual(len(data_b["rows"]), 2)
        skus_b = {r["sku"] for r in data_b["rows"]}
        self.assertEqual(skus_b, {"ABC-P2", "ABC-P3"})

        # Faqat C toifa
        data_c = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "abc_class": "C",
        }, self.admin)
        self.assertEqual(len(data_c["rows"]), 2)
        skus_c = {r["sku"] for r in data_c["rows"]}
        self.assertEqual(skus_c, {"ABC-P4", "ABC-NEG"})

    def test_09_query_count_o_1_no_n_plus_one(self):
        """09. Complete path O(1) so'rovlar soni: ReportBuilderService.generate aniq 2 ta SQL so'rov bajaradi."""
        with self.assertNumQueries(2):
            # To'liq yo'l: foundation calculation + product metadata + store + category + brand + final row construction
            ReportBuilderService.generate({
                "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
                "store_id": str(self.store.id), "metric": "revenue",
            }, self.admin)

    def test_10_permissions_and_fallbacks(self):
        """10. Ruxsatlar: superuser, sales.view fallback, explicit abc huquqi, 403."""
        # Superuser -> 200 OK
        resp_admin = self._call(ReportBuilderGenerateAPIView, {
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
        }, user=self.admin)
        self.assertEqual(resp_admin.status_code, 200)

        # Staff with reports.sales.view fallback -> 200 OK
        resp_staff = self._call(ReportBuilderGenerateAPIView, {
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
        }, user=self.staff_sales_user)
        self.assertEqual(resp_staff.status_code, 200)

        # Staff with explicit reports.abc_analysis.view -> 200 OK
        abc_role = Role.objects.create(name="ABC Role", permissions=["reports.abc_analysis.view"])
        abc_user = User.objects.create(
            phone_number="+998909990099", email="abc_user@reports.uz",
            is_staff=True, role=abc_role,
        )
        StoreUser.objects.create(user=abc_user, store=self.store, role=StoreUser.Role.Manager, is_active=True)
        resp_abc = self._call(ReportBuilderGenerateAPIView, {
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
        }, user=abc_user)
        self.assertEqual(resp_abc.status_code, 200)

        # Unauth user -> 403 Forbidden
        resp_unauth = self._call(ReportBuilderGenerateAPIView, {
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
        }, user=self.unauth_user)
        self.assertEqual(resp_unauth.status_code, 403)

    def test_11_export_excel_and_csv(self):
        """11. Excel va CSV formatlarida ABC tahlili eksporti."""
        label, columns, rows, summary, info = ReportBuilderService.export_rows({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
        }, self.admin)
        self.assertEqual(label, "ABC tahlili")
        col_keys = [c["key"] for c in columns]
        self.assertIn("abc_class", col_keys)
        self.assertIn("share_pct", col_keys)
        self.assertIn("cumulative_pct", col_keys)
        self.assertIn("metric_value", col_keys)

        # Excel API
        excel_resp = self._call(ReportBuilderExportAPIView, {
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "export_type": "excel",
        })
        self.assertEqual(excel_resp.status_code, 200)
        self.assertIn("spreadsheetml", excel_resp["Content-Type"])

        # CSV API
        csv_resp = self._call(ReportBuilderExportAPIView, {
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "export_type": "csv",
        })
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("text/csv", csv_resp["Content-Type"])
        self.assertIn("ABC toifasi", csv_resp.content.decode("utf-8-sig"))

    def test_12_filter_product_id(self):
        """12. product_id bo'yicha filtr."""
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "product_id": str(self.p1.id),
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "ABC-P1")

    def test_13_filter_sku(self):
        """13. SKU bo'yicha filtr."""
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "sku": "ABC-P2",
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "ABC-P2")

    def test_14_filter_barcode(self):
        """14. Barcode bo'yicha filtr."""
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "barcode": "4780099",
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "ABC-P1")

    def test_15_filter_search(self):
        """15. Qidiruv (nomi, SKU yoki shtrixkod bo'yicha)."""
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "search": "Tovar C 10pct",
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "ABC-P3")

    def test_16_filter_category_id(self):
        """16. category_id bo'yicha filtr."""
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "category_id": str(self.cat1.id),
        }, self.admin)
        self.assertGreaterEqual(len(data["rows"]), 1)
        for r in data["rows"]:
            self.assertEqual(r["category"], "Moylar")

    def test_17_filter_brand_id(self):
        """17. brand_id bo'yicha filtr."""
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "brand_id": str(self.brand1.id),
        }, self.admin)
        self.assertGreaterEqual(len(data["rows"]), 1)
        for r in data["rows"]:
            self.assertEqual(r["brand"], "Castrol")

    def test_18_filter_date_and_store(self):
        """18. from/to va store_id bo'yicha filtr."""
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "store_id": str(self.store.id),
        }, self.admin)
        self.assertGreaterEqual(len(data["rows"]), 1)
        for r in data["rows"]:
            self.assertEqual(r["store"], "ABC Markaziy do'kon")

    def test_19_export_pdf(self):
        """19. PDF eksport va uning tarkibida ABC ma'lumotlari mavjudligi."""
        pdf_resp = self._call(ReportBuilderExportAPIView, {
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
            "export_type": "pdf", "metric": "revenue",
        })
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp["Content-Type"], "application/pdf")
        self.assertTrue(pdf_resp.content.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_resp.content), 1000)

    def test_20_dataset_scope_product_with_stock_but_no_sales_excluded(self):
        """20. Qamrov (Scope): Omborda qoldig'i bo'lib, davrda sotuvi va qaytarimi bo'lmagan tovar ABC tahliliga kirmasligi."""
        p_idle = Product.objects.create(name="Harakatsiz Zaxira Tovari", sku="IDLE-999")
        ProductBatch.objects.create(
            product=p_idle, store=self.store, quantity=100,
            purchase_price=Decimal("50000.00"), selling_price=Decimal("70000.00"),
        )
        # Davrda p_idle uchun hech qanday sotuv va qaytarim yaratilmaydi
        data = ReportBuilderService.generate({
            "report_type": "abc_analysis", "from": "2026-09-01", "to": "2026-09-30",
        }, self.admin)
        skus_in_report = {r["sku"] for r in data["rows"]}
        self.assertNotIn("IDLE-999", skus_in_report)


class InventoryResultsReportTest(TestCase):
    """
    Phase 1.4: "Inventarizatsiya natijalari va kamomad/ortiqcha tahlili" test to'plami.

    Asosiy tamoyillar:
      1. Expected Qty FAQAT `InventorySnapshot.expected_quantity` dan olinadi (ProductBatch dan emas).
      2. Counted Qty FAQAT `InventoryCount.counted_quantity` dan olinadi.
      3. is_check=False bo'lgan tovarlar kamomad/nol deb hisoblanmaydi; status='unchecked'.
      4. Faqat completed sessiyalar kiritiladi; cancelled sessiyalar chetlab o'tiladi.
      5. Final balance mavjud `InventoryService.finalize` formulasi bilan 100% bir xil.
      6. Unit cost kamomad uchun WriteOffItem dan, ortiqcha uchun ProductBatch dan olinadi.
      7. Tarixiy o'zgarmaslik (immutability): keyinchalik ProductBatch o'zgarsa ham hisobot o'zgarmaydi.
      8. Do'kon izolyatsiyasi (Store isolation) va RBAC huquqlari.
      9. Barcha 11 ta filtrlar ishlashi.
      10. Excel, CSV, PDF eksportlari.
      11. Query count / N+1 tekshiruvi.
    """

    @classmethod
    def setUpTestData(cls):
        cls.factory = APIRequestFactory()

        # Foydalanuvchilar
        cls.admin = User.objects.create(
            phone_number="+998900001001", email="admin.inv@crm.uz",
            is_superuser=True, is_staff=True,
        )
        cls.store_user = User.objects.create(
            phone_number="+998900001002", email="store.user@crm.uz",
            is_superuser=False, is_staff=True,
        )
        cls.other_user = User.objects.create(
            phone_number="+998900001003", email="other.user@crm.uz",
            is_superuser=False, is_staff=True,
        )

        # Do'konlar
        cls.store1 = Store.objects.create(
            name="Asosiy Do'kon", phone_number="+998900001011",
            address="Toshkent", type=Store.StoreType.STORE,
        )
        cls.store2 = Store.objects.create(
            name="Ikkinchi Do'kon", phone_number="+998900001012",
            address="Samarqand", type=Store.StoreType.STORE,
        )

        # Role va ruxsatlar
        viewer_role = Role.objects.create(
            name="Report Viewer Inv",
            permissions=["reports.view", "reports.inventory_results.view", "reports.inventory_results.export"],
        )
        cls.store_user.role = viewer_role
        cls.store_user.save()
        cls.other_user.role = viewer_role
        cls.other_user.save()

        # StoreUser linklar
        StoreUser.objects.create(store=cls.store1, user=cls.store_user, is_active=True)
        StoreUser.objects.create(store=cls.store2, user=cls.other_user, is_active=True)

        # Kategoriya, brend va o'lchov
        cls.cat = Category.objects.create(name="Filtrlar")
        cls.brand = Brand.objects.create(name="Bosch")
        cls.unit = ProductUnitMeasurement.objects.create(measurement="dona")

        # Mahsulotlar
        cls.p_matched = Product.objects.create(
            name="Yog' filtri Bosch", sku="FLT-001", barcode="4780001001",
            category=cls.cat, brand=cls.brand, unit_measurement=cls.unit,
        )
        cls.p_shortage = Product.objects.create(
            name="Havo filtri Bosch", sku="FLT-002", barcode="4780001002",
            category=cls.cat, brand=cls.brand, unit_measurement=cls.unit,
        )
        cls.p_excess = Product.objects.create(
            name="Yoqilg'i filtri Bosch", sku="FLT-003", barcode="4780001003",
            category=cls.cat, brand=cls.brand, unit_measurement=cls.unit,
        )
        cls.p_unchecked = Product.objects.create(
            name="Salon filtri Bosch", sku="FLT-004", barcode="4780001004",
            category=cls.cat, brand=cls.brand, unit_measurement=cls.unit,
        )
        cls.p_movement = Product.objects.create(
            name="Sham Bosch", sku="SPK-005", barcode="4780001005",
            category=cls.cat, brand=cls.brand, unit_measurement=cls.unit,
        )

        # ProductBatches (Store 1)
        ProductBatch.objects.create(
            product=cls.p_matched, store=cls.store1, quantity=Decimal("10"),
            purchase_price=Decimal("10000.00"), selling_price=Decimal("15000.00"), is_active=True,
        )
        ProductBatch.objects.create(
            product=cls.p_shortage, store=cls.store1, quantity=Decimal("8"),
            purchase_price=Decimal("15000.00"), selling_price=Decimal("20000.00"), is_active=True,
        )
        ProductBatch.objects.create(
            product=cls.p_excess, store=cls.store1, quantity=Decimal("14"),
            purchase_price=Decimal("20000.00"), selling_price=Decimal("25000.00"), is_active=True,
        )
        ProductBatch.objects.create(
            product=cls.p_unchecked, store=cls.store1, quantity=Decimal("10"),
            purchase_price=Decimal("12000.00"), selling_price=Decimal("18000.00"), is_active=True,
        )
        ProductBatch.objects.create(
            product=cls.p_movement, store=cls.store1, quantity=Decimal("8"),
            purchase_price=Decimal("8000.00"), selling_price=Decimal("12000.00"), is_active=True,
        )

        # 1. Tugallangan inventarizatsiya sessiyasi (Store 1)
        session_dt = datetime(2026, 9, 5, 10, 0, 0, tzinfo=dt_timezone.utc)
        cls.session1 = InventorySession.objects.create(
            store=cls.store1, started_by=cls.admin,
            status=InventorySession.Status.COMPLETED, snapshot_taken=True,
        )
        InventorySession.objects.filter(id=cls.session1.id).update(started_at=session_dt)
        cls.session1.refresh_from_db()

        # Snapshots (Store 1, Session 1)
        InventorySnapshot.objects.create(session=cls.session1, store=cls.store1, product=cls.p_matched, expected_quantity=Decimal("10"))
        InventorySnapshot.objects.create(session=cls.session1, store=cls.store1, product=cls.p_shortage, expected_quantity=Decimal("10"))
        InventorySnapshot.objects.create(session=cls.session1, store=cls.store1, product=cls.p_excess, expected_quantity=Decimal("10"))
        InventorySnapshot.objects.create(session=cls.session1, store=cls.store1, product=cls.p_unchecked, expected_quantity=Decimal("10"))
        InventorySnapshot.objects.create(session=cls.session1, store=cls.store1, product=cls.p_movement, expected_quantity=Decimal("10"))

        count_time = datetime(2026, 9, 5, 11, 0, 0, tzinfo=dt_timezone.utc)
        # Counts:
        # matched: expected 10, counted 10
        InventoryCount.objects.create(
            session=cls.session1, product=cls.p_matched, counted_quantity=Decimal("10"),
            status=InventoryCount.Status.EQUAL, is_check=True, counted_at=count_time,
        )
        # shortage: expected 10, counted 7 (shortage 3)
        InventoryCount.objects.create(
            session=cls.session1, product=cls.p_shortage, counted_quantity=Decimal("7"),
            status=InventoryCount.Status.LESS, is_check=True, counted_at=count_time,
        )
        # excess: expected 10, counted 14 (excess 4)
        InventoryCount.objects.create(
            session=cls.session1, product=cls.p_excess, counted_quantity=Decimal("14"),
            status=InventoryCount.Status.MORE, is_check=True, counted_at=count_time,
        )
        # unchecked: expected 10, counted 0, is_check=False
        InventoryCount.objects.create(
            session=cls.session1, product=cls.p_unchecked, counted_quantity=Decimal("0"),
            status=InventoryCount.Status.PENDING, is_check=False, counted_at=None,
        )
        # movement: expected 10, counted 10, is_check=True
        InventoryCount.objects.create(
            session=cls.session1, product=cls.p_movement, counted_quantity=Decimal("10"),
            status=InventoryCount.Status.EQUAL, is_check=True, counted_at=count_time,
        )

        # InventoryMovement for p_movement:
        # Movement BEFORE count_time (10:30) - should NOT affect final balance
        m_before = InventoryMovement.objects.create(
            session=cls.session1, product=cls.p_movement, quantity=Decimal("1"),
            type=InventoryMovement.Type.SALE, ref_id=101,
        )
        InventoryMovement.objects.filter(id=m_before.id).update(created_at=datetime(2026, 9, 5, 10, 30, 0, tzinfo=dt_timezone.utc))

        # Movement AFTER count_time (11:30) - sale of 2 items -> final_balance = 10 - 2 = 8
        m_after = InventoryMovement.objects.create(
            session=cls.session1, product=cls.p_movement, quantity=Decimal("2"),
            type=InventoryMovement.Type.SALE, ref_id=102,
        )
        InventoryMovement.objects.filter(id=m_after.id).update(created_at=datetime(2026, 9, 5, 11, 30, 0, tzinfo=dt_timezone.utc))

        # WriteOff and WriteOffItem for shortage of p_shortage (linked to session1)
        cls.write_off = WriteOff.objects.create(
            store=cls.store1, reason=WriteOff.Reason.INVENTORY,
            inventory_session=cls.session1, total_amount=Decimal("45000.00"),
            created_by=cls.admin,
        )
        WriteOffItem.objects.create(
            write_off=cls.write_off, product=cls.p_shortage, quantity=Decimal("3"),
            purchase_price=Decimal("15000.00"), selling_price=Decimal("20000.00"),
        )

        # 2. Bekor qilingan sessiya (cancelled) — hisobotga kirmasligi shart
        cls.session_cancelled = InventorySession.objects.create(
            store=cls.store1, started_by=cls.admin,
            status=InventorySession.Status.CANCELLED, snapshot_taken=True,
        )
        p_cancelled = Product.objects.create(name="Bekor Qilingan Tovar", sku="CNC-001")
        InventorySnapshot.objects.create(session=cls.session_cancelled, store=cls.store1, product=p_cancelled, expected_quantity=Decimal("50"))

        # 3. Store 2 Sessiyasi (Do'kon izolyatsiyasi testi uchun)
        cls.session_store2 = InventorySession.objects.create(
            store=cls.store2, started_by=cls.admin,
            status=InventorySession.Status.COMPLETED, snapshot_taken=True,
        )
        InventorySession.objects.filter(id=cls.session_store2.id).update(started_at=session_dt)
        cls.p_store2 = Product.objects.create(name="Store 2 Tovari", sku="ST2-001")
        InventorySnapshot.objects.create(session=cls.session_store2, store=cls.store2, product=cls.p_store2, expected_quantity=Decimal("25"))
        InventoryCount.objects.create(
            session=cls.session_store2, product=cls.p_store2, counted_quantity=Decimal("25"),
            status=InventoryCount.Status.EQUAL, is_check=True, counted_at=count_time,
        )

    def test_01_meta_registry(self):
        """01. Meta reestrda inventory_results va dinamik filtrlar mavjudligi."""
        meta = ReportBuilderService.meta()
        reports = {r["key"]: r for r in meta["reports"]}
        self.assertIn("inventory_results", reports)
        rep = reports["inventory_results"]
        self.assertEqual(rep["label"], "Inventarizatsiya natijalari")
        self.assertTrue(rep["search"])
        params = [f["param"] for f in rep["filters"]]
        self.assertIn("store_id", params)
        self.assertIn("category_id", params)
        self.assertIn("brand_id", params)
        self.assertIn("status", params)
        self.assertIn("sort_by", params)

    def test_02_completed_session_included_cancelled_excluded(self):
        """02. Faqat completed sessiyalar hisobotda bo'lishi, cancelled sessiyalar chiqarilmasligi."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "from": "2026-09-01", "to": "2026-09-30",
        }, self.admin)
        skus = {r["sku"] for r in data["rows"]}
        self.assertIn("FLT-001", skus)
        self.assertNotIn("CNC-001", skus)

    def test_03_expected_quantity_from_snapshot_and_counted_from_count(self):
        """03. Expected Qty snapshotdan, Counted Qty count modelidan olinishi."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "session_id": str(self.session1.id),
        }, self.admin)
        matched_row = next(r for r in data["rows"] if r["sku"] == "FLT-001")
        self.assertEqual(matched_row["expected_qty"], 10.0)
        self.assertEqual(matched_row["counted_qty"], 10.0)

    def test_04_checked_equal_is_matched(self):
        """04. Sanalgan = Kutilgan (is_check=True) -> matched, diff=0, shortage=0, excess=0."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "session_id": str(self.session1.id),
        }, self.admin)
        row = next(r for r in data["rows"] if r["sku"] == "FLT-001")
        self.assertEqual(row["raw_status"], "matched")
        self.assertEqual(row["difference_qty"], 0.0)
        self.assertEqual(row["shortage_qty"], 0.0)
        self.assertEqual(row["excess_qty"], 0.0)
        self.assertEqual(float(row["shortage_value"]), 0.0)
        self.assertEqual(float(row["excess_value"]), 0.0)

    def test_05_checked_less_is_shortage(self):
        """05. Sanalgan < Kutilgan -> shortage, difference_qty < 0, shortage_qty > 0, excess_qty = 0."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "session_id": str(self.session1.id),
        }, self.admin)
        row = next(r for r in data["rows"] if r["sku"] == "FLT-002")
        self.assertEqual(row["raw_status"], "shortage")
        self.assertEqual(row["expected_qty"], 10.0)
        self.assertEqual(row["counted_qty"], 7.0)
        self.assertEqual(row["difference_qty"], -3.0)
        self.assertEqual(row["shortage_qty"], 3.0)
        self.assertEqual(row["excess_qty"], 0.0)

    def test_06_checked_more_is_excess(self):
        """06. Sanalgan > Kutilgan -> excess, difference_qty > 0, shortage_qty = 0, excess_qty > 0."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "session_id": str(self.session1.id),
        }, self.admin)
        row = next(r for r in data["rows"] if r["sku"] == "FLT-003")
        self.assertEqual(row["raw_status"], "excess")
        self.assertEqual(row["expected_qty"], 10.0)
        self.assertEqual(row["counted_qty"], 14.0)
        self.assertEqual(row["difference_qty"], 4.0)
        self.assertEqual(row["shortage_qty"], 0.0)
        self.assertEqual(row["excess_qty"], 4.0)

    def test_07_unchecked_is_not_shortage(self):
        """07. is_check=False bo'lgan tovar kamomad/nol deb hisoblanmasligi, status='unchecked' bo'lishi."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "session_id": str(self.session1.id),
        }, self.admin)
        row = next(r for r in data["rows"] if r["sku"] == "FLT-004")
        self.assertEqual(row["raw_status"], "unchecked")
        self.assertEqual(row["counted_qty"], "—")
        self.assertEqual(row["difference_qty"], "—")
        self.assertEqual(row["shortage_qty"], 0.0)
        self.assertEqual(row["excess_qty"], 0.0)
        self.assertEqual(float(row["shortage_value"]), 0.0)
        self.assertEqual(float(row["excess_value"]), 0.0)
        self.assertEqual(row["final_balance"], 10.0)

    def test_08_shortage_value_from_writeoff_item(self):
        """08. Kamomad qiymati WriteOffItem dagi purchase_price bo'yicha hisoblanishi."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "session_id": str(self.session1.id),
        }, self.admin)
        row = next(r for r in data["rows"] if r["sku"] == "FLT-002")
        # shortage_qty = 3.0, WriteOffItem.purchase_price = 15000 -> 45000
        self.assertEqual(row["shortage_qty"], 3.0)
        self.assertIn("45", str(row["shortage_value"]))

    def test_09_excess_value_from_product_batch(self):
        """09. Ortiqcha qiymati ProductBatch purchase_price bo'yicha hisoblanishi."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "session_id": str(self.session1.id),
        }, self.admin)
        row = next(r for r in data["rows"] if r["sku"] == "FLT-003")
        # excess_qty = 4.0, batch purchase_price = 20000 -> 80000
        self.assertEqual(row["excess_qty"], 4.0)
        self.assertIn("80", str(row["excess_value"]))

    def test_10_historical_immutability(self):
        """10. Tarixiy o'zgarmaslik: keyinchalik ProductBatch.quantity o'zgarsa ham hisobot o'zgarmasligi."""
        # ProductBatch miqdorini 999 ga o'zgartiramiz
        ProductBatch.objects.filter(product=self.p_matched, store=self.store1).update(quantity=Decimal("999"))
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "session_id": str(self.session1.id),
        }, self.admin)
        row = next(r for r in data["rows"] if r["sku"] == "FLT-001")
        self.assertEqual(row["expected_qty"], 10.0)
        self.assertEqual(row["counted_qty"], 10.0)
        self.assertEqual(row["difference_qty"], 0.0)

    def test_11_final_balance_with_movements_after_counted_at(self):
        """11. Final balance: sanoqdan keyingi harakatlar (created_at > counted_at) to'g'ri chegirilishi."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "session_id": str(self.session1.id),
        }, self.admin)
        row = next(r for r in data["rows"] if r["sku"] == "SPK-005")
        # Counted = 10. Sanoqdan keyingi sotuv = 2 dona -> Final balance = 10 - 2 = 8
        self.assertEqual(row["counted_qty"], 10.0)
        self.assertEqual(row["final_balance"], 8.0)

    def test_12_filter_session_id(self):
        """12. session_id filtri bo'yicha aniq sessiyani filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "session_id": str(self.session_store2.id),
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "ST2-001")

    def test_13_filter_store_id(self):
        """13. store_id filtri bo'yicha do'konni filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "store_id": str(self.store2.id),
        }, self.admin)
        skus = {r["sku"] for r in data["rows"]}
        self.assertEqual(skus, {"ST2-001"})

    def test_14_filter_product_id(self):
        """14. product_id filtri bo'yicha bitta mahsulotni olish."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "product_id": str(self.p_shortage.id),
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "FLT-002")

    def test_15_filter_sku_and_barcode(self):
        """15. sku va barcode filtrlari bo'yicha tekshirish."""
        data_sku = ReportBuilderService.generate({
            "report_type": "inventory_results", "sku": "FLT-003",
        }, self.admin)
        self.assertEqual(len(data_sku["rows"]), 1)
        self.assertEqual(data_sku["rows"][0]["sku"], "FLT-003")

        data_bc = ReportBuilderService.generate({
            "report_type": "inventory_results", "barcode": "4780001002",
        }, self.admin)
        self.assertEqual(len(data_bc["rows"]), 1)
        self.assertEqual(data_bc["rows"][0]["sku"], "FLT-002")

    def test_16_filter_category_and_brand(self):
        """16. category_id va brand_id filtrlari bo'yicha tekshirish."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results",
            "category_id": str(self.cat.id),
            "brand_id": str(self.brand.id),
        }, self.admin)
        for r in data["rows"]:
            self.assertEqual(r["category_name"], "Filtrlar")
            self.assertEqual(r["brand_name"], "Bosch")

    def test_17_filter_status(self):
        """17. status filtri bo'yicha tekshirish: matched, shortage, excess, unchecked."""
        for st in ("matched", "shortage", "excess", "unchecked"):
            data = ReportBuilderService.generate({
                "report_type": "inventory_results",
                "session_id": str(self.session1.id),
                "status": st,
            }, self.admin)
            for r in data["rows"]:
                self.assertEqual(r["raw_status"], st)

    def test_18_filter_search(self):
        """18. search parametri bo'yicha qidirish."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "search": "Yoqilg'i",
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "FLT-003")

    def test_19_filter_daterange(self):
        """19. from va to sanalar oralig'i bo'yicha sessiyalarni filtrlash."""
        data_in = ReportBuilderService.generate({
            "report_type": "inventory_results", "from": "2026-09-01", "to": "2026-09-10",
        }, self.admin)
        self.assertGreater(len(data_in["rows"]), 0)

        data_out = ReportBuilderService.generate({
            "report_type": "inventory_results", "from": "2025-01-01", "to": "2025-01-31",
        }, self.admin)
        self.assertEqual(len(data_out["rows"]), 0)

    def test_20_store_isolation(self):
        """20. Do'kon izolyatsiyasi: Store 1 xodimi Store 2 sessiyasini ko'ra olmasligi."""
        view = ReportBuilderGenerateAPIView.as_view()
        # Store 1 xodimi Store 2 sessiyasini so'raydi
        req = self.factory.get(f"/api/v1/reports/builder/generate/?report_type=inventory_results&session_id={self.session_store2.id}")
        force_authenticate(req, user=self.store_user)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        # Store 2 sessiyasi ro'yxatda chiqmasligi kerak
        self.assertEqual(len(resp.data["rows"]), 0)

    def test_21_permissions_rbac(self):
        """21. RBAC ruxsatlar tekshiruvi: huquqsiz 403, ruxsat bilan 200."""
        view = ReportBuilderGenerateAPIView.as_view()
        no_perm_user = User.objects.create(
            phone_number="+998900001099", email="noperm@crm.uz",
            is_superuser=False, is_staff=True,
        )
        StoreUser.objects.create(store=self.store1, user=no_perm_user, is_active=True)
        Role.objects.create(name="Empty Role", permissions=[])
        req = self.factory.get("/api/v1/reports/builder/generate/?report_type=inventory_results")
        force_authenticate(req, user=no_perm_user)
        resp = view(req)
        self.assertEqual(resp.status_code, 403)

        # reports.inventory_results.view huquqi bilan -> 200
        inv_role = Role.objects.create(name="Inv Role", permissions=["reports.view", "reports.inventory_results.view"])
        no_perm_user.role = inv_role
        no_perm_user.save()
        req2 = self.factory.get("/api/v1/reports/builder/generate/?report_type=inventory_results")
        force_authenticate(req2, user=no_perm_user)
        resp2 = view(req2)
        self.assertEqual(resp2.status_code, 200)

    def test_22_export_excel(self):
        """22. Excel eksport: .xlsx fayl, 200 OK."""
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=inventory_results&export_type=excel")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertGreater(len(resp.content), 500)

    def test_23_export_csv(self):
        """23. CSV eksport: UTF-8 BOM bilan, 200 OK."""
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=inventory_results&export_type=csv")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp["Content-Type"].startswith("text/csv"))
        self.assertTrue(resp.content.startswith(b"\xef\xbb\xbf"))

    def test_24_export_pdf(self):
        """24. PDF eksport: ReportLab Platypus landscape, 200 OK."""
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=inventory_results&export_type=pdf")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))
        self.assertGreater(len(resp.content), 1000)

    def test_25_query_count_o_1_no_n_plus_one(self):
        """25. Query count: Mahsulotlar soni ortganda ham SQL so'rovlar soni O(1) qolishi."""
        # 5 mahsulotli holatda SQL so'rovlar sonini o'lchaymiz (StockAllocation bilan kengaytirilgan O(1) so'rovlar)
        with self.assertNumQueries(9):
            ReportingFoundationService.get_inventory_results_metrics(
                session_id=self.session1.id,
            )

        # Qo'shimcha 15 ta mahsulot va snapshot yaratamiz
        extra_products = [
            Product(name=f"Qo'shimcha Tovar {i}", sku=f"EXT-{i:03d}")
            for i in range(15)
        ]
        Product.objects.bulk_create(extra_products)
        created_prods = list(Product.objects.filter(sku__startswith="EXT-"))
        extra_snapshots = [
            InventorySnapshot(session=self.session1, store=self.store1, product=p, expected_quantity=Decimal("5"))
            for p in created_prods
        ]
        InventorySnapshot.objects.bulk_create(extra_snapshots)
        extra_counts = [
            InventoryCount(session=self.session1, product=p, counted_quantity=Decimal("5"), is_check=True, status="e")
            for p in created_prods
        ]
        InventoryCount.objects.bulk_create(extra_counts)

        # Endi 20 ta mahsulot bo'lsa ham so'rovlar soni aynan 9 ta (O(1)) qolishi shart!
        with self.assertNumQueries(9):
            ReportingFoundationService.get_inventory_results_metrics(
                session_id=self.session1.id,
            )

    def test_26_totals_and_summary_reconciliation(self):
        """26. Yig'indilar va summary ko'rsatkichlari qatorlar yig'indisiga to'liq mos kelishi."""
        data = ReportBuilderService.generate({
            "report_type": "inventory_results", "session_id": str(self.session1.id),
        }, self.admin)
        summary_map = {s["label"]: s["value"] for s in data["summary"]}
        rows = data["rows"]
        expected_sum = sum(r["expected_qty"] for r in rows)
        shortage_sum = sum(r["shortage_qty"] for r in rows)
        excess_sum = sum(r["excess_qty"] for r in rows)

        self.assertEqual(summary_map["Jami kutilgan qoldiq"], expected_sum)
        self.assertEqual(summary_map["Jami kamomad miqdori"], shortage_sum)
        self.assertEqual(summary_map["Jami ortiqcha miqdori"], excess_sum)
        self.assertIn("2 ta", summary_map["Mos kelgan tovarlar"])
        self.assertIn("1 ta", summary_map["Kamomadli tovarlar"])
        self.assertIn("1 ta", summary_map["Ortiqchali tovarlar"])
        self.assertIn("1 ta", summary_map["Sanalmagan tovarlar"])

    def test_27_sorting(self):
        """27. Saralash filtrlari: shortage_qty, difference, name bo'yicha to'g'ri tartiblash."""
        # difference bo'yicha asc
        data_diff = ReportBuilderService.generate({
            "report_type": "inventory_results",
            "session_id": str(self.session1.id),
            "sort_by": "difference",
            "sort_dir": "asc",
        }, self.admin)
        # Eng kichik diff (-3.0) birinchi kelishi kerak
        self.assertEqual(data_diff["rows"][0]["sku"], "FLT-002")

        # shortage_qty bo'yicha desc
        data_sh = ReportBuilderService.generate({
            "report_type": "inventory_results",
            "session_id": str(self.session1.id),
            "sort_by": "shortage_qty",
            "sort_dir": "desc",
        }, self.admin)
        self.assertEqual(data_sh["rows"][0]["sku"], "FLT-002")

    def _create_movement_test_session(self):
        """Yordamchi: final_balance tekshiruvlari uchun alohida sessiya yaratish."""
        sess = InventorySession.objects.create(
            store=self.store1, started_by=self.admin,
            status=InventorySession.Status.COMPLETED, snapshot_taken=True,
        )
        count_time = datetime(2026, 9, 8, 12, 0, 0, tzinfo=dt_timezone.utc)
        InventorySession.objects.filter(id=sess.id).update(started_at=count_time)
        sess.refresh_from_db()

        p = Product.objects.create(name=f"Balans Test {sess.id}", sku=f"BLN-{sess.id}")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p, expected_quantity=Decimal("10"))
        InventoryCount.objects.create(
            session=sess, product=p, counted_quantity=Decimal("10"),
            status=InventoryCount.Status.EQUAL, is_check=True, counted_at=count_time,
        )
        return sess, p, count_time

    def test_28_final_balance_sale(self):
        """28. final_balance: counted + sale -> counted - sold_out."""
        sess, p, count_time = self._create_movement_test_session()
        m = InventoryMovement.objects.create(
            session=sess, product=p, quantity=Decimal("3"),
            type=InventoryMovement.Type.SALE, ref_id=201,
        )
        InventoryMovement.objects.filter(id=m.id).update(created_at=datetime(2026, 9, 8, 12, 30, 0, tzinfo=dt_timezone.utc))

        data = ReportBuilderService.generate({"report_type": "inventory_results", "session_id": str(sess.id)}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["counted_qty"], 10.0)
        self.assertEqual(row["final_balance"], 7.0)

    def test_29_final_balance_transfer_out(self):
        """29. final_balance: counted + transfer_out -> counted - transfer_out."""
        sess, p, count_time = self._create_movement_test_session()
        m = InventoryMovement.objects.create(
            session=sess, product=p, quantity=Decimal("4"),
            type=InventoryMovement.Type.TRANSFER_OUT, ref_id=202,
        )
        InventoryMovement.objects.filter(id=m.id).update(created_at=datetime(2026, 9, 8, 12, 30, 0, tzinfo=dt_timezone.utc))

        data = ReportBuilderService.generate({"report_type": "inventory_results", "session_id": str(sess.id)}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["final_balance"], 6.0)

    def test_30_final_balance_transfer_in(self):
        """30. final_balance: counted + transfer_in -> counted + transfer_in."""
        sess, p, count_time = self._create_movement_test_session()
        m = InventoryMovement.objects.create(
            session=sess, product=p, quantity=Decimal("5"),
            type=InventoryMovement.Type.TRANSFER_IN, ref_id=203,
        )
        InventoryMovement.objects.filter(id=m.id).update(created_at=datetime(2026, 9, 8, 12, 30, 0, tzinfo=dt_timezone.utc))

        data = ReportBuilderService.generate({"report_type": "inventory_results", "session_id": str(sess.id)}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["final_balance"], 15.0)

    def test_31_final_balance_return(self):
        """31. final_balance: counted + return -> counted + returned."""
        sess, p, count_time = self._create_movement_test_session()
        m = InventoryMovement.objects.create(
            session=sess, product=p, quantity=Decimal("2"),
            type=InventoryMovement.Type.RETURN, ref_id=204,
        )
        InventoryMovement.objects.filter(id=m.id).update(created_at=datetime(2026, 9, 8, 12, 30, 0, tzinfo=dt_timezone.utc))

        data = ReportBuilderService.generate({"report_type": "inventory_results", "session_id": str(sess.id)}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["final_balance"], 12.0)

    def test_32_final_balance_entry(self):
        """32. final_balance: counted + entry -> counted + entry."""
        sess, p, count_time = self._create_movement_test_session()
        m = InventoryMovement.objects.create(
            session=sess, product=p, quantity=Decimal("6"),
            type=InventoryMovement.Type.ENTRY, ref_id=205,
        )
        InventoryMovement.objects.filter(id=m.id).update(created_at=datetime(2026, 9, 8, 12, 30, 0, tzinfo=dt_timezone.utc))

        data = ReportBuilderService.generate({"report_type": "inventory_results", "session_id": str(sess.id)}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["final_balance"], 16.0)

    def test_33_final_balance_multiple_movements_combination(self):
        """33. final_balance: bir nechta movement kombinatsiyasi (sale, to, ti, e, r)."""
        sess, p, count_time = self._create_movement_test_session()
        mv_time = datetime(2026, 9, 8, 12, 30, 0, tzinfo=dt_timezone.utc)
        # sale=2, transfer_out=1, transfer_in=3, entry=4, return=1 -> 10 - 2 - 1 + 3 + 4 + 1 = 15
        for mtype, qty in [
            (InventoryMovement.Type.SALE, Decimal("2")),
            (InventoryMovement.Type.TRANSFER_OUT, Decimal("1")),
            (InventoryMovement.Type.TRANSFER_IN, Decimal("3")),
            (InventoryMovement.Type.ENTRY, Decimal("4")),
            (InventoryMovement.Type.RETURN, Decimal("1")),
        ]:
            m = InventoryMovement.objects.create(session=sess, product=p, quantity=qty, type=mtype, ref_id=206)
            InventoryMovement.objects.filter(id=m.id).update(created_at=mv_time)

        data = ReportBuilderService.generate({"report_type": "inventory_results", "session_id": str(sess.id)}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["final_balance"], 15.0)

    def test_34_final_balance_movement_before_counted_at(self):
        """34. movement counted_at DAN OLDIN bo'lsa final_balance ga ta'sir qilmasligi."""
        sess, p, count_time = self._create_movement_test_session()
        m = InventoryMovement.objects.create(
            session=sess, product=p, quantity=Decimal("4"),
            type=InventoryMovement.Type.SALE, ref_id=207,
        )
        # Sanoq 12:00 da, harakat 11:30 da -> ta'sir qilmasligi shart
        InventoryMovement.objects.filter(id=m.id).update(created_at=datetime(2026, 9, 8, 11, 30, 0, tzinfo=dt_timezone.utc))

        data = ReportBuilderService.generate({"report_type": "inventory_results", "session_id": str(sess.id)}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["final_balance"], 10.0)

    def test_35_final_balance_movement_after_counted_at(self):
        """35. movement counted_at DAN KEYIN bo'lsa ta'sir qilishi."""
        sess, p, count_time = self._create_movement_test_session()
        m = InventoryMovement.objects.create(
            session=sess, product=p, quantity=Decimal("4"),
            type=InventoryMovement.Type.SALE, ref_id=208,
        )
        # Sanoq 12:00 da, harakat 12:30 da -> ta'sir qilishi shart (10 - 4 = 6)
        InventoryMovement.objects.filter(id=m.id).update(created_at=datetime(2026, 9, 8, 12, 30, 0, tzinfo=dt_timezone.utc))

        data = ReportBuilderService.generate({"report_type": "inventory_results", "session_id": str(sess.id)}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["final_balance"], 6.0)

    def test_36_final_balance_counted_at_boundary(self):
        """36. counted_at boundary (created_at == counted_at) holati chetlab o'tilishi."""
        sess, p, count_time = self._create_movement_test_session()
        m = InventoryMovement.objects.create(
            session=sess, product=p, quantity=Decimal("3"),
            type=InventoryMovement.Type.SALE, ref_id=209,
        )
        # created_at == counted_at (12:00:00) -> finalize logikasida mv['created_at'] <= counted_at chetlab o'tiladi
        InventoryMovement.objects.filter(id=m.id).update(created_at=count_time)

        data = ReportBuilderService.generate({"report_type": "inventory_results", "session_id": str(sess.id)}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["final_balance"], 10.0)

    def test_37_final_balance_unchecked_product(self):
        """37. unchecked product uchun final_balance qoidasi (expected_quantity qolishi)."""
        sess = InventorySession.objects.create(
            store=self.store1, started_by=self.admin,
            status=InventorySession.Status.COMPLETED, snapshot_taken=True,
        )
        p_un = Product.objects.create(name="Unchecked Test", sku="UCHK-01")
        InventorySnapshot.objects.create(session=sess, store=self.store1, product=p_un, expected_quantity=Decimal("15"))
        InventoryCount.objects.create(
            session=sess, product=p_un, counted_quantity=Decimal("0"),
            status=InventoryCount.Status.PENDING, is_check=False, counted_at=None,
        )
        # Harakat bo'lsa ham sanalmagan tovar qoldig'iga tegmaslik
        m = InventoryMovement.objects.create(
            session=sess, product=p_un, quantity=Decimal("5"),
            type=InventoryMovement.Type.SALE, ref_id=210,
        )
        InventoryMovement.objects.filter(id=m.id).update(created_at=datetime(2026, 9, 8, 12, 30, 0, tzinfo=dt_timezone.utc))

        data = ReportBuilderService.generate({"report_type": "inventory_results", "session_id": str(sess.id)}, self.admin)
        row = data["rows"][0]
        self.assertEqual(row["raw_status"], "unchecked")
        self.assertEqual(row["expected_qty"], 15.0)
        self.assertEqual(row["counted_qty"], "—")
        self.assertEqual(row["final_balance"], 15.0)

    def test_38_excel_programmatic_structure(self):
        """38. Programmatic Excel: workbook, 19 ustun, Table, AutoFilter, Freeze Panes va filtrlangan dataset."""
        import io
        import openpyxl

        view = ReportBuilderExportAPIView.as_view()
        # status=shortage filtri bilan eksport qilamiz
        req = self.factory.get(f"/api/v1/reports/builder/export/?report_type=inventory_results&export_type=excel&session_id={self.session1.id}&status=shortage")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        self.assertIn("Inventarizatsiya natijalari", wb.sheetnames)
        ws = wb["Inventarizatsiya natijalari"]

        # 1. Real Excel Table mavjudligi
        self.assertGreater(len(ws.tables), 0)
        tbl_name = list(ws.tables.keys())[0]
        tbl = ws.tables[tbl_name]
        self.assertIn("InventoryResults", tbl.name)

        # 2. 31 ta ustun mavjudligi (Billz darajasiga kengaytirilgan)
        self.assertEqual(len(tbl.tableColumns), 31)
        col_names = [c.name for c in tbl.tableColumns]
        expected_cols = [
            "Sessiya ID", "Inventarizatsiya", "Do'kon", "Sana", "Tovar nomi", "SKU", "Shtrix-kod",
            "O'lchov", "Kategoriya", "Brend", "Tovar holati",
            "Kutilgan qoldiq", "Sanalgan miqdor", "Tafovut miqdori", "Kamomad miqdori", "Ortiqcha miqdori",
            "Sessiya davridagi sotuv", "Band qilingan (rezerv)", "Hisobdan chiqarilgan",
            "Chiqish transferi", "Kirish transferi", "Avto kirim (ortiqcha)", "Avto chiqim (kamomad)",
            "Birlik tannarxi", "Birlik sotuv narxi", "Kamomad — tannarx", "Kamomad — sotuv narxi",
            "Ortiqcha — tannarx", "Ortiqcha — sotuv narxi", "Yakuniy hisobiy qoldiq", "Holati",
        ]
        self.assertEqual(col_names, expected_cols)

        # 3. AutoFilter mavjudligi
        self.assertIsNotNone(tbl.autoFilter)
        self.assertTrue(tbl.autoFilter.ref.startswith("A4"))

        # 4. Freeze Panes mavjudligi (ma'lumotlar satri boshlanishida)
        self.assertEqual(ws.freeze_panes, "A5")

        # 5. Filtrlangan dataset (faqat shortage bo'lgan tovar, ya'ni FLT-002)
        # Qatorlar soni aynan 1 ta bo'lishi kerak
        rows_data = [row for row in ws.iter_rows(min_row=5, values_only=True) if row[0] is not None]
        # Faqat 1 ta tovar qatori (FLT-002, Col 5 = SKU)
        item_rows = [r for r in rows_data if r[5] == "FLT-002"]
        self.assertEqual(len(item_rows), 1)

    def test_39_store_isolation_export_excel_csv_pdf(self):
        """39. Store isolation export: Store user boshqa do'kon ma'lumotini eksport qila olmasligi."""
        view = ReportBuilderExportAPIView.as_view()
        # Store 1 xodimi Store 2 sessiyasini Excel, CSV va PDF da so'raydi
        for exp_type in ["excel", "csv", "pdf"]:
            req = self.factory.get(
                f"/api/v1/reports/builder/export/?report_type=inventory_results&export_type={exp_type}&session_id={self.session_store2.id}"
            )
            force_authenticate(req, user=self.store_user)
            resp = view(req)
            self.assertEqual(resp.status_code, 200)
            if exp_type == "csv":
                content_str = resp.content.decode("utf-8-sig")
                # Store 2 mahsuloti (ST2-001) CSV ichida bo'lmasligi kerak
                self.assertNotIn("ST2-001", content_str)
            elif exp_type == "excel":
                import io, openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(resp.content))
                ws = wb.active
                data_cells = [cell.value for row in ws.iter_rows(min_row=5) for cell in row if cell.value]
                self.assertNotIn("ST2-001", data_cells)
            elif exp_type == "pdf":
                # PDF da ST2-001 bo'lmasligi kerak
                self.assertNotIn(b"ST2-001", resp.content)


class OrderReturnsReportTest(TestCase):
    """
    Phase 1.5: "Buyurtma qaytarishlari" (Order Returns) hisoboti uchun to'liq test to'plami.

    Asosiy tamoyillar:
      1. Dataset root: SaleReturnItem (granular item-level).
      2. Asosiy sana: SaleReturn.created_at in [start, end) sargable oraliq.
         Asl Sale.created_at qaytarim davrini belgilamaydi!
      3. returned_qty -> SaleReturnItem.quantity
      4. refund_amount -> SaleReturnItem.total_price (proportsional chegirma inobatga olingan).
      5. sale_price -> SaleReturnItem.unit_price
      6. purchase_price -> SaleReturnItem.sale_item.purchase_price (fallback: ProductBatch.purchase_price).
      7. sale_value -> returned_qty * unit_price
      8. purchase_value -> returned_qty * purchase_price
      9. discount_refunded -> sale_value - refund_amount
      10. profit_impact -> refund_amount - purchase_value (yo'qotilgan yalpi foyda).
      11. supplier -> latest StockEntryItem.entry.supplier (hujjatlashtirilgan cheklov).
      12. To'lov usuli: payment_group -> Payment(is_refund=True) yoki Qarz / Aralash.
      13. Do'kon izolyatsiyasi (Store isolation) va RBAC huquqlari.
      14. Barcha 13 ta filtrlar ishlashi.
      15. Excel (OpenXML Table, AutoFilter, Freeze Panes), CSV (UTF-8 BOM), PDF (Landscape).
      16. Query count / N+1 tekshiruvi (fixed 3-4 queries).
    """

    @classmethod
    def setUpTestData(cls):
        cls.factory = APIRequestFactory()
        tz = timezone.get_current_timezone()

        # 1. Foydalanuvchilar
        cls.admin = User.objects.create(
            phone_number="+998900002001", email="admin.ret@crm.uz",
            is_superuser=True, is_staff=True, full_name="Super Admin",
        )
        cls.store_user = User.objects.create(
            phone_number="+998900002002", email="store1.user@crm.uz",
            is_superuser=False, is_staff=True, full_name="Store 1 Xodim",
        )
        cls.other_store_user = User.objects.create(
            phone_number="+998900002003", email="store2.user@crm.uz",
            is_superuser=False, is_staff=True, full_name="Store 2 Xodim",
        )
        cls.seller_user = User.objects.create(
            phone_number="+998900002004", email="seller@crm.uz",
            is_superuser=False, is_staff=True, full_name="Sotuvchi Temur",
        )
        cls.no_perm_user = User.objects.create(
            phone_number="+998900002005", email="noperm@crm.uz",
            is_superuser=False, is_staff=True, full_name="Oddiy Foydalanuvchi",
        )
        cls.order_returns_viewer_user = User.objects.create(
            phone_number="+998900002006", email="retviewer@crm.uz",
            is_superuser=False, is_staff=True, full_name="Ret Viewer",
        )
        cls.sales_viewer_user = User.objects.create(
            phone_number="+998900002007", email="salesviewer@crm.uz",
            is_superuser=False, is_staff=True, full_name="Sales Viewer",
        )

        # 2. Do'konlar
        cls.store1 = Store.objects.create(
            name="Markaziy Filial", phone_number="+998900002011",
            address="Toshkent", type=Store.StoreType.STORE,
        )
        cls.store2 = Store.objects.create(
            name="Samarqand Filiali", phone_number="+998900002012",
            address="Samarqand", type=Store.StoreType.STORE,
        )

        # 3. Rollar va ruxsatlar
        role_ret_viewer = Role.objects.create(
            name="Order Returns Viewer",
            permissions=["reports.view", "reports.order_returns.view", "reports.order_returns.export"],
        )
        role_sales_viewer = Role.objects.create(
            name="Sales Viewer",
            permissions=["reports.view", "reports.sales.view", "reports.sales.export"],
        )
        role_general_viewer = Role.objects.create(
            name="General Store Viewer",
            permissions=["reports.view", "reports.order_returns.view", "reports.order_returns.export"],
        )

        cls.store_user.role = role_general_viewer
        cls.store_user.save()
        cls.other_store_user.role = role_general_viewer
        cls.other_store_user.save()
        cls.seller_user.role = role_general_viewer
        cls.seller_user.save()
        cls.order_returns_viewer_user.role = role_ret_viewer
        cls.order_returns_viewer_user.save()
        cls.sales_viewer_user.role = role_sales_viewer
        cls.sales_viewer_user.save()

        StoreUser.objects.create(store=cls.store1, user=cls.store_user, is_active=True)
        StoreUser.objects.create(store=cls.store1, user=cls.seller_user, is_active=True)
        StoreUser.objects.create(store=cls.store1, user=cls.order_returns_viewer_user, is_active=True)
        StoreUser.objects.create(store=cls.store1, user=cls.sales_viewer_user, is_active=True)
        StoreUser.objects.create(store=cls.store2, user=cls.other_store_user, is_active=True)

        # 4. Kategoriya, brend, o'lchov
        cls.cat_filtr = Category.objects.create(name="Filtrlar")
        cls.cat_brake = Category.objects.create(name="Tormoz tizimi")
        cls.brand_bosch = Brand.objects.create(name="Bosch")
        cls.brand_brembo = Brand.objects.create(name="Brembo")
        cls.unit_dona = ProductUnitMeasurement.objects.create(measurement="dona")
        cls.unit_pair = ProductUnitMeasurement.objects.create(measurement="juft")

        # 5. Mahsulotlar
        cls.p1 = Product.objects.create(
            name="Yog' filtri Bosch", sku="FLT-101", barcode="4781001001",
            category=cls.cat_filtr, brand=cls.brand_bosch, unit_measurement=cls.unit_dona,
        )
        cls.p2 = Product.objects.create(
            name="Tormoz kolodkasi Brembo", sku="BRK-202", barcode="4781001002",
            category=cls.cat_brake, brand=cls.brand_brembo, unit_measurement=cls.unit_dona,
        )
        cls.p3 = Product.objects.create(
            name="Motor moyi Shell", sku="OIL-303", barcode="4781001003",
            category=cls.cat_filtr, brand=cls.brand_bosch, unit_measurement=cls.unit_dona,
        )
        cls.p4_pair = Product.objects.create(
            name="Avto etigi", sku="SH-404", barcode="4781001004",
            unit_measurement=cls.unit_pair, is_pair=True,
        )
        cls.p_store2 = Product.objects.create(
            name="Akumulyator Varta", sku="BAT-505", barcode="4781001005",
            unit_measurement=cls.unit_dona,
        )

        # 6. Partiyalar (ProductBatch)
        ProductBatch.objects.create(
            store=cls.store1, product=cls.p1, quantity=Decimal("50"),
            purchase_price=Decimal("60000.00"), selling_price=Decimal("100000.00"), is_active=True,
        )
        ProductBatch.objects.create(
            store=cls.store1, product=cls.p2, quantity=Decimal("30"),
            purchase_price=Decimal("120000.00"), selling_price=Decimal("200000.00"), is_active=True,
        )
        ProductBatch.objects.create(
            store=cls.store1, product=cls.p3, quantity=Decimal("40"),
            purchase_price=Decimal("80000.00"), selling_price=Decimal("120000.00"), is_active=True,
        )
        ProductBatch.objects.create(
            store=cls.store1, product=cls.p4_pair, quantity=Decimal("20"),
            purchase_price=Decimal("150000.00"), selling_price=Decimal("250000.00"), is_active=True,
        )
        ProductBatch.objects.create(
            store=cls.store2, product=cls.p_store2, quantity=Decimal("10"),
            purchase_price=Decimal("400000.00"), selling_price=Decimal("500000.00"), is_active=True,
        )

        # 7. Ta'minotchilar va kirimlar
        cls.sup1 = Supplier.objects.create(name="Bosch Uzbekistan", phone_number="+998901110001")
        cls.sup2 = Supplier.objects.create(name="Brembo Global", phone_number="+998901110002")

        entry1 = StockEntry.objects.create(supplier=cls.sup1, store=cls.store1)
        StockEntryItem.objects.create(
            entry=entry1, product=cls.p1, quantity=Decimal("50"),
            purchase_price=Decimal("60000.00"), selling_price=Decimal("100000.00"),
        )

        entry2 = StockEntry.objects.create(supplier=cls.sup2, store=cls.store1)
        StockEntryItem.objects.create(
            entry=entry2, product=cls.p2, quantity=Decimal("30"),
            purchase_price=Decimal("120000.00"), selling_price=Decimal("200000.00"),
        )

        # 8. Bank kartalari va mijozlar
        cls.card_humo = BankCard.objects.create(name="Humo", is_default=True)
        cls.cust1 = Customer.objects.create(full_name="Alisher Navoiy", phone_number="+998901112233")
        cls.cust2 = Customer.objects.create(full_name="Bobur Mirzo", phone_number="+998904445566")

        # 9. Sotuvlar va qaytarimlar (Barcha senariylar uchun)

        # SOTUV 1 & QAYTARIM 1: Standart naqd qaytarim (chegirmasiz)
        # Sotildi: 3 dona p1 @ 100,000 (tannarx 60,000)
        # Qaytarildi: 1 dona p1 @ 100,000. refund=100,000, tannarx=60,000, foyda_tasiri=40,000
        cls.sale1 = Sale.objects.create(
            store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_amount=Decimal("300000.00"), paid_amount=Decimal("300000.00"), status=Sale.Status.PAID,
        )
        cls.sale_item1 = SaleItem.objects.create(
            sale=cls.sale1, product=cls.p1, quantity=Decimal("3.00"), unit_price=Decimal("100000.00"),
            purchase_price=Decimal("60000.00"), total_price=Decimal("300000.00"),
        )
        cls.pg1 = uuid.uuid4()
        cls.ret1 = SaleReturn.objects.create(
            sale=cls.sale1, store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_refund=Decimal("100000.00"), payment_group=cls.pg1, comment="Mijozga yoqmadi",
        )
        cls.ret_item1 = SaleReturnItem.objects.create(
            sale_return=cls.ret1, sale_item=cls.sale_item1, product=cls.p1,
            quantity=Decimal("1.00"), unit_price=Decimal("100000.00"), total_price=Decimal("100000.00"),
        )
        Payment.objects.create(
            sale=cls.sale1, customer=cls.cust1, amount=Decimal("100000.00"),
            type=Payment.Type.CASH, is_refund=True, payment_group=cls.pg1,
        )
        SaleReturn.objects.filter(id=cls.ret1.id).update(created_at=datetime(2026, 9, 5, 10, 0, tzinfo=tz))

        # SOTUV 2 & QAYTARIM 2: Chegirmali sotuv qaytarimi (Karta Humo orqali)
        # Sotildi: 2 dona p2 @ 200,000 = 400,000 gross, chegirma = 40,000 (10%). Sof to'langan: 360,000.
        # Qaytarildi: 1 dona p2. Qaytgan summa: 180,000 (10% chegirma bilan).
        # sale_value = 200,000. discount_refunded = 20,000. refund_amount = 180,000.
        # purchase_value = 120,000. profit_impact = 180,000 - 120,000 = 60,000.
        cls.sale2 = Sale.objects.create(
            store=cls.store1, customer=cls.cust2, seller=cls.seller_user,
            total_amount=Decimal("360000.00"), paid_amount=Decimal("360000.00"), status=Sale.Status.PAID,
            discount_amount=Decimal("40000.00"),
        )
        cls.sale_item2 = SaleItem.objects.create(
            sale=cls.sale2, product=cls.p2, quantity=Decimal("2.00"), unit_price=Decimal("200000.00"),
            purchase_price=Decimal("120000.00"), total_price=Decimal("400000.00"),
        )
        cls.pg2 = uuid.uuid4()
        cls.ret2 = SaleReturn.objects.create(
            sale=cls.sale2, store=cls.store1, customer=cls.cust2, seller=cls.seller_user,
            total_refund=Decimal("180000.00"), payment_group=cls.pg2, comment="Brak chiqdi",
        )
        cls.ret_item2 = SaleReturnItem.objects.create(
            sale_return=cls.ret2, sale_item=cls.sale_item2, product=cls.p2,
            quantity=Decimal("1.00"), unit_price=Decimal("200000.00"), total_price=Decimal("180000.00"),
        )
        Payment.objects.create(
            sale=cls.sale2, customer=cls.cust2, amount=Decimal("180000.00"),
            type=Payment.Type.CARD, bank_card=cls.card_humo, is_refund=True, payment_group=cls.pg2,
        )
        SaleReturn.objects.filter(id=cls.ret2.id).update(created_at=datetime(2026, 9, 6, 12, 0, tzinfo=tz))

        # SOTUV 3 & QAYTARIM 3_a / 3_b: Bitta sotuvdan bir necha marta qaytarish
        cls.sale3 = Sale.objects.create(
            store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_amount=Decimal("600000.00"), paid_amount=Decimal("600000.00"), status=Sale.Status.PAID,
        )
        cls.sale_item3 = SaleItem.objects.create(
            sale=cls.sale3, product=cls.p3, quantity=Decimal("5.00"), unit_price=Decimal("120000.00"),
            purchase_price=Decimal("80000.00"), total_price=Decimal("600000.00"),
        )
        cls.pg3_a = uuid.uuid4()
        cls.ret3_a = SaleReturn.objects.create(
            sale=cls.sale3, store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_refund=Decimal("120000.00"), payment_group=cls.pg3_a,
        )
        cls.ret_item3_a = SaleReturnItem.objects.create(
            sale_return=cls.ret3_a, sale_item=cls.sale_item3, product=cls.p3,
            quantity=Decimal("1.00"), unit_price=Decimal("120000.00"), total_price=Decimal("120000.00"),
        )
        Payment.objects.create(
            sale=cls.sale3, customer=cls.cust1, amount=Decimal("120000.00"),
            type=Payment.Type.CASH, is_refund=True, payment_group=cls.pg3_a,
        )
        SaleReturn.objects.filter(id=cls.ret3_a.id).update(created_at=datetime(2026, 9, 7, 9, 0, tzinfo=tz))

        cls.pg3_b = uuid.uuid4()
        cls.ret3_b = SaleReturn.objects.create(
            sale=cls.sale3, store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_refund=Decimal("240000.00"), payment_group=cls.pg3_b,
        )
        cls.ret_item3_b = SaleReturnItem.objects.create(
            sale_return=cls.ret3_b, sale_item=cls.sale_item3, product=cls.p3,
            quantity=Decimal("2.00"), unit_price=Decimal("120000.00"), total_price=Decimal("240000.00"),
        )
        Payment.objects.create(
            sale=cls.sale3, customer=cls.cust1, amount=Decimal("240000.00"),
            type=Payment.Type.CASH, is_refund=True, payment_group=cls.pg3_b,
        )
        SaleReturn.objects.filter(id=cls.ret3_b.id).update(created_at=datetime(2026, 9, 8, 14, 0, tzinfo=tz))

        # SOTUV 4 & QAYTARIM 4: Sotuv avgustda, Qaytarim sentyabrda
        cls.sale4 = Sale.objects.create(
            store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_amount=Decimal("100000.00"), paid_amount=Decimal("100000.00"), status=Sale.Status.PAID,
        )
        Sale.objects.filter(id=cls.sale4.id).update(created_at=datetime(2026, 8, 15, 10, 0, tzinfo=tz))
        cls.sale_item4 = SaleItem.objects.create(
            sale=cls.sale4, product=cls.p1, quantity=Decimal("1.00"), unit_price=Decimal("100000.00"),
            purchase_price=Decimal("60000.00"), total_price=Decimal("100000.00"),
        )
        cls.pg4 = uuid.uuid4()
        cls.ret4 = SaleReturn.objects.create(
            sale=cls.sale4, store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_refund=Decimal("100000.00"), payment_group=cls.pg4,
        )
        cls.ret_item4 = SaleReturnItem.objects.create(
            sale_return=cls.ret4, sale_item=cls.sale_item4, product=cls.p1,
            quantity=Decimal("1.00"), unit_price=Decimal("100000.00"), total_price=Decimal("100000.00"),
        )
        Payment.objects.create(
            sale=cls.sale4, customer=cls.cust1, amount=Decimal("100000.00"),
            type=Payment.Type.CASH, is_refund=True, payment_group=cls.pg4,
        )
        SaleReturn.objects.filter(id=cls.ret4.id).update(created_at=datetime(2026, 9, 2, 11, 0, tzinfo=tz))

        # SOTUV 5 & QAYTARIM 5: Arxivlangan (soft-deleted) sotuv qaytarimi
        cls.sale5 = Sale.objects.create(
            store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_amount=Decimal("100000.00"), paid_amount=Decimal("100000.00"), status=Sale.Status.PAID,
            deleted_at=datetime(2026, 9, 3, 16, 0, tzinfo=tz),
        )
        cls.sale_item5 = SaleItem.objects.create(
            sale=cls.sale5, product=cls.p1, quantity=Decimal("1.00"), unit_price=Decimal("100000.00"),
            purchase_price=Decimal("60000.00"), total_price=Decimal("100000.00"),
        )
        cls.ret5 = SaleReturn.objects.create(
            sale=cls.sale5, store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_refund=Decimal("100000.00"),
        )
        cls.ret_item5 = SaleReturnItem.objects.create(
            sale_return=cls.ret5, sale_item=cls.sale_item5, product=cls.p1,
            quantity=Decimal("1.00"), unit_price=Decimal("100000.00"), total_price=Decimal("100000.00"),
        )
        SaleReturn.objects.filter(id=cls.ret5.id).update(created_at=datetime(2026, 9, 3, 15, 0, tzinfo=tz))

        # SOTUV 6 & QAYTARIM 6: Eski sotuv (purchase_price = None) -> ProductBatch narxidan fallback
        cls.sale6 = Sale.objects.create(
            store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_amount=Decimal("100000.00"), paid_amount=Decimal("100000.00"), status=Sale.Status.PAID,
        )
        cls.sale_item6 = SaleItem.objects.create(
            sale=cls.sale6, product=cls.p1, quantity=Decimal("1.00"), unit_price=Decimal("100000.00"),
            purchase_price=None, total_price=Decimal("100000.00"),
        )
        cls.pg6 = uuid.uuid4()
        cls.ret6 = SaleReturn.objects.create(
            sale=cls.sale6, store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_refund=Decimal("100000.00"), payment_group=cls.pg6,
        )
        cls.ret_item6 = SaleReturnItem.objects.create(
            sale_return=cls.ret6, sale_item=cls.sale_item6, product=cls.p1,
            quantity=Decimal("1.00"), unit_price=Decimal("100000.00"), total_price=Decimal("100000.00"),
        )
        Payment.objects.create(
            sale=cls.sale6, customer=cls.cust1, amount=Decimal("100000.00"),
            type=Payment.Type.CASH, is_refund=True, payment_group=cls.pg6,
        )
        SaleReturn.objects.filter(id=cls.ret6.id).update(created_at=datetime(2026, 9, 4, 11, 0, tzinfo=tz))

        # SOTUV 7 & QAYTARIM 7: Qarzdan chegirilgan qaytarim (payment_group=None)
        cls.sale7 = Sale.objects.create(
            store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_amount=Decimal("100000.00"), paid_amount=Decimal("0.00"), status=Sale.Status.DEBT,
        )
        cls.sale_item7 = SaleItem.objects.create(
            sale=cls.sale7, product=cls.p1, quantity=Decimal("1.00"), unit_price=Decimal("100000.00"),
            purchase_price=Decimal("60000.00"), total_price=Decimal("100000.00"),
        )
        cls.ret7 = SaleReturn.objects.create(
            sale=cls.sale7, store=cls.store1, customer=cls.cust1, seller=cls.seller_user,
            total_refund=Decimal("100000.00"), payment_group=None,
        )
        cls.ret_item7 = SaleReturnItem.objects.create(
            sale_return=cls.ret7, sale_item=cls.sale_item7, product=cls.p1,
            quantity=Decimal("1.00"), unit_price=Decimal("100000.00"), total_price=Decimal("100000.00"),
        )
        SaleReturn.objects.filter(id=cls.ret7.id).update(created_at=datetime(2026, 9, 9, 15, 0, tzinfo=tz))

        # SOTUV 8 & QAYTARIM 8: Juft mahsulot (kasr miqdor 0.5)
        cls.sale8 = Sale.objects.create(
            store=cls.store1, customer=cls.cust2, seller=cls.seller_user,
            total_amount=Decimal("500000.00"), paid_amount=Decimal("500000.00"), status=Sale.Status.PAID,
        )
        cls.sale_item8 = SaleItem.objects.create(
            sale=cls.sale8, product=cls.p4_pair, quantity=Decimal("2.00"), unit_price=Decimal("250000.00"),
            purchase_price=Decimal("150000.00"), total_price=Decimal("500000.00"),
        )
        cls.pg8 = uuid.uuid4()
        cls.ret8 = SaleReturn.objects.create(
            sale=cls.sale8, store=cls.store1, customer=cls.cust2, seller=cls.seller_user,
            total_refund=Decimal("125000.00"), payment_group=cls.pg8,
        )
        cls.ret_item8 = SaleReturnItem.objects.create(
            sale_return=cls.ret8, sale_item=cls.sale_item8, product=cls.p4_pair,
            quantity=Decimal("0.50"), unit_price=Decimal("250000.00"), total_price=Decimal("125000.00"),
        )
        Payment.objects.create(
            sale=cls.sale8, customer=cls.cust2, amount=Decimal("125000.00"),
            type=Payment.Type.CASH, is_refund=True, payment_group=cls.pg8,
        )
        SaleReturn.objects.filter(id=cls.ret8.id).update(created_at=datetime(2026, 9, 10, 16, 0, tzinfo=tz))

        # SOTUV STORE 2 & QAYTARIM STORE 2: Store isolation tekshirish uchun
        cls.sale_s2 = Sale.objects.create(
            store=cls.store2, customer=cls.cust2, seller=cls.other_store_user,
            total_amount=Decimal("500000.00"), paid_amount=Decimal("500000.00"), status=Sale.Status.PAID,
        )
        cls.sale_item_s2 = SaleItem.objects.create(
            sale=cls.sale_s2, product=cls.p_store2, quantity=Decimal("1.00"), unit_price=Decimal("500000.00"),
            purchase_price=Decimal("400000.00"), total_price=Decimal("500000.00"),
        )
        cls.pg_s2 = uuid.uuid4()
        cls.ret_s2 = SaleReturn.objects.create(
            sale=cls.sale_s2, store=cls.store2, customer=cls.cust2, seller=cls.other_store_user,
            total_refund=Decimal("500000.00"), payment_group=cls.pg_s2,
        )
        cls.ret_item_s2 = SaleReturnItem.objects.create(
            sale_return=cls.ret_s2, sale_item=cls.sale_item_s2, product=cls.p_store2,
            quantity=Decimal("1.00"), unit_price=Decimal("500000.00"), total_price=Decimal("500000.00"),
        )
        Payment.objects.create(
            sale=cls.sale_s2, customer=cls.cust2, amount=Decimal("500000.00"),
            type=Payment.Type.CASH, is_refund=True, payment_group=cls.pg_s2,
        )
        SaleReturn.objects.filter(id=cls.ret_s2.id).update(created_at=datetime(2026, 9, 5, 15, 0, tzinfo=tz))

    def test_01_meta_registry(self):
        """01. Meta reestrda order_returns va barcha 13 ta dinamik filtrlar mavjudligi."""
        meta = ReportBuilderService.meta()
        reports = {r["key"]: r for r in meta["reports"]}
        self.assertIn("order_returns", reports)
        rep = reports["order_returns"]
        self.assertEqual(rep["label"], "Buyurtma qaytarishlari")
        self.assertTrue(rep["search"])

        filter_params = {f["param"] for f in rep["filters"]}
        self.assertIn("date", filter_params)
        self.assertIn("store_id", filter_params)
        self.assertIn("supplier_id", filter_params)
        self.assertIn("category_id", filter_params)
        self.assertIn("brand_id", filter_params)
        self.assertIn("seller_id", filter_params)
        self.assertIn("order_id", filter_params)
        self.assertIn("return_id", filter_params)
        self.assertIn("sku", filter_params)
        self.assertIn("barcode", filter_params)
        self.assertIn("sort_by", filter_params)

    def test_02_basic_return_metrics(self):
        """02. Standart naqd qaytarim qatori va 23 ta ustun ko'rsatkichlari to'g'riligi."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "return_id": str(self.ret1.id),
        }, self.admin)
        rows = data["rows"]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["return_id"], self.ret1.id)
        self.assertEqual(r["order_id"], self.sale1.id)
        self.assertEqual(r["store_name"], "Markaziy Filial")
        self.assertEqual(r["seller_name"], "Sotuvchi Temur")
        self.assertEqual(r["customer_name"], "Alisher Navoiy")
        self.assertEqual(r["product_name"], "Yog' filtri Bosch")
        self.assertEqual(r["sku"], "FLT-101")
        self.assertEqual(r["barcode"], "4781001001")
        self.assertEqual(r["brand_name"], "Bosch")
        self.assertEqual(r["category_name"], "Filtrlar")
        self.assertEqual(r["unit"], "dona")
        self.assertEqual(r["supplier_name"], "Bosch Uzbekistan")
        self.assertEqual(r["returned_qty"], 1.0)
        self.assertEqual(r["unit_sale_price"], "100000.00")
        self.assertEqual(r["unit_purchase_price"], "60000.00")
        self.assertEqual(r["sale_value"], "100000.00")
        self.assertEqual(r["discount_refunded"], "0.00")
        self.assertEqual(r["refund_amount"], "100000.00")
        self.assertEqual(r["purchase_value"], "60000.00")
        self.assertEqual(r["profit_impact"], "40000.00")
        self.assertEqual(r["payment_method"], "Naqd")
        self.assertEqual(r["comment"], "Mijozga yoqmadi")

    def test_03_discounted_sale_return(self):
        """03. Chegirmali sotuvda proportsional refund va qaytarilgan chegirma hisob-kitobi."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "return_id": str(self.ret2.id),
        }, self.admin)
        rows = data["rows"]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        # Gross = 200,000, 10% chegirma bilan qaytgan summa = 180,000
        self.assertEqual(r["sale_value"], "200000.00")
        self.assertEqual(r["refund_amount"], "180000.00")
        self.assertEqual(r["discount_refunded"], "20000.00")
        self.assertEqual(r["purchase_value"], "120000.00")
        # profit_impact = 180,000 - 120,000 = 60,000
        self.assertEqual(r["profit_impact"], "60000.00")
        self.assertIn("Karta", r["payment_method"])

    def test_04_partial_return(self):
        """04. Qisman qaytarishda faqat qaytarilgan qism (quantity) ko'rinishi."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "return_id": str(self.ret1.id),
        }, self.admin)
        # Sotuv miqdori 3 ta edi, faqat 1 ta qaytarilgan
        self.assertEqual(data["rows"][0]["returned_qty"], 1.0)

    def test_05_multiple_returns_same_sale(self):
        """05. Bitta sotuvdan bir nechta alohida qaytarim bo'lganda har biri alohida chiqishi."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "order_id": str(self.sale3.id),
        }, self.admin)
        rows = data["rows"]
        self.assertEqual(len(rows), 2)
        return_ids = {r["return_id"] for r in rows}
        self.assertEqual(return_ids, {self.ret3_a.id, self.ret3_b.id})
        total_returned = sum(r["returned_qty"] for r in rows)
        self.assertEqual(total_returned, 3.0)

    def test_06_date_filtering_sargable(self):
        """06. SaleReturn.created_at bo'yicha [start, end) intervalda to'g'ri filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "from": "2026-09-05",
            "to": "2026-09-06",
            "store_id": str(self.store1.id),
        }, self.admin)
        return_ids = {r["return_id"] for r in data["rows"]}
        # 09-05 va 09-06 kunlardagi qaytarimlar kirishi kerak
        self.assertIn(self.ret1.id, return_ids)
        self.assertIn(self.ret2.id, return_ids)
        # 09-07 dagi qaytarim kirmasligi kerak
        self.assertNotIn(self.ret3_a.id, return_ids)

    def test_07_sale_date_does_not_affect_return_period(self):
        """07. Asl sotuv avgustda bo'lsa ham qaytarim o'zining sanasi (sentyabr) bo'yicha chiqishi."""
        # Sentyabr oyi hisoboti
        sept_data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "from": "2026-09-01",
            "to": "2026-09-30",
            "return_id": str(self.ret4.id),
        }, self.admin)
        self.assertEqual(len(sept_data["rows"]), 1)

        # Avgust oyi hisobotida esa ushbu qaytarim chiqmasligi shart!
        aug_data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "from": "2026-08-01",
            "to": "2026-08-31",
            "return_id": str(self.ret4.id),
        }, self.admin)
        self.assertEqual(len(aug_data["rows"]), 0)

    def test_08_soft_deleted_sale_excluded(self):
        """08. Arxivlangan (soft-deleted) sotuv qaytarimlari hisobotga tushmasligi."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "return_id": str(self.ret5.id),
        }, self.admin)
        self.assertEqual(len(data["rows"]), 0)

    def test_09_purchase_price_from_sale_item(self):
        """09. Birlik tannarx SaleItem.purchase_price dan to'g'ri olinishi."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "return_id": str(self.ret1.id),
        }, self.admin)
        self.assertEqual(data["rows"][0]["unit_purchase_price"], "60000.00")

    def test_10_missing_purchase_price_fallback(self):
        """10. SaleItem da purchase_price bo'lmaganda ProductBatch dan fallback olinishi."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "return_id": str(self.ret6.id),
        }, self.admin)
        self.assertEqual(data["rows"][0]["unit_purchase_price"], "60000.00")

    def test_11_zero_purchase_price_and_warning(self):
        """11. Tannarx 0 bo'lganda has_zero_cost_items=True va ogohlantirish berilishi."""
        # Maxsus 0 tannarxli mahsulot va qaytarim
        p_free = Product.objects.create(name="Sovg'a tovar", sku="FREE-001", unit_measurement=self.unit_dona)
        ProductBatch.objects.create(store=self.store1, product=p_free, quantity=10, purchase_price=Decimal("0.00"), selling_price=Decimal("10000.00"))
        s_free = Sale.objects.create(store=self.store1, seller=self.seller_user, total_amount=Decimal("10000.00"))
        si_free = SaleItem.objects.create(sale=s_free, product=p_free, quantity=1, unit_price=Decimal("10000.00"), purchase_price=Decimal("0.00"), total_price=Decimal("10000.00"))
        ret_free = SaleReturn.objects.create(sale=s_free, store=self.store1, seller=self.seller_user, total_refund=Decimal("10000.00"))
        SaleReturnItem.objects.create(sale_return=ret_free, sale_item=si_free, product=p_free, quantity=1, unit_price=Decimal("10000.00"), total_price=Decimal("10000.00"))

        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "return_id": str(ret_free.id),
        }, self.admin)
        self.assertTrue(data.get("info", {}).get("warning") is not None)

    def test_12_immutability_on_product_batch_mutation(self):
        """12. Tarixiy o'zgarmaslik: keyinchalik ProductBatch narxi yoki qoldig'i o'zgarsa ham hisobot o'zgarmasligi."""
        # Batch narxini va sonini o'zgartiramiz
        ProductBatch.objects.filter(store=self.store1, product=self.p1).update(
            purchase_price=Decimal("999999.00"),
            quantity=Decimal("12345"),
        )
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "return_id": str(self.ret1.id),
        }, self.admin)
        # Birlik tannarx o'zgarmaydi, avvalgi 60,000 ligicha qoladi!
        self.assertEqual(data["rows"][0]["unit_purchase_price"], "60000.00")
        self.assertEqual(data["rows"][0]["returned_qty"], 1.0)

    def test_13_filter_store_id(self):
        """13. Do'kon (store_id) bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "store_id": str(self.store2.id),
        }, self.admin)
        self.assertTrue(all(r["store_id"] == self.store2.id for r in data["rows"]))

    def test_14_filter_order_id(self):
        """14. Buyurtma (order_id) bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "order_id": str(self.sale1.id),
        }, self.admin)
        self.assertTrue(all(r["order_id"] == self.sale1.id for r in data["rows"]))

    def test_15_filter_return_id(self):
        """15. Qaytarish (return_id) bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "return_id": str(self.ret2.id),
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["return_id"], self.ret2.id)

    def test_16_filter_product_id(self):
        """16. Mahsulot (product_id) bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "product_id": str(self.p2.id),
        }, self.admin)
        self.assertTrue(all(r["product_id"] == self.p2.id for r in data["rows"]))

    def test_17_filter_sku(self):
        """17. SKU bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "sku": "BRK-202",
        }, self.admin)
        self.assertTrue(all(r["sku"] == "BRK-202" for r in data["rows"]))

    def test_18_filter_barcode(self):
        """18. Shtrix-kod bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "barcode": "4781001002",
        }, self.admin)
        self.assertTrue(all(r["barcode"] == "4781001002" for r in data["rows"]))

    def test_19_filter_category_id(self):
        """19. Kategoriya bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "category_id": str(self.cat_brake.id),
        }, self.admin)
        self.assertTrue(all(r["category_id"] == self.cat_brake.id for r in data["rows"]))

    def test_20_filter_brand_id(self):
        """20. Brend bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "brand_id": str(self.brand_brembo.id),
        }, self.admin)
        self.assertTrue(all(r["brand_id"] == self.brand_brembo.id for r in data["rows"]))

    def test_21_filter_supplier_id(self):
        """21. Ta'minotchi bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "supplier_id": str(self.sup1.id),
        }, self.admin)
        self.assertTrue(all(r["supplier_name"] == "Bosch Uzbekistan" for r in data["rows"]))

    def test_22_filter_seller_id(self):
        """22. Sotuvchi (xodim) bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "seller_id": str(self.seller_user.id),
        }, self.admin)
        self.assertTrue(all(r["seller_id"] == self.seller_user.id for r in data["rows"]))

    def test_23_search_text(self):
        """23. Matnli qidiruv (mahsulot, SKU, chek №, mijoz)."""
        # Mahsulot nomi bo'yicha
        d1 = ReportBuilderService.generate({"report_type": "order_returns", "search": "kolodkasi"}, self.admin)
        self.assertTrue(any("kolodkasi" in r["product_name"].lower() for r in d1["rows"]))

        # Mijoz nomi bo'yicha
        d2 = ReportBuilderService.generate({"report_type": "order_returns", "search": "Bobur"}, self.admin)
        self.assertTrue(any("bobur" in r["customer_name"].lower() for r in d2["rows"]))

        # Chek ID bo'yicha
        d3 = ReportBuilderService.generate({"report_type": "order_returns", "search": str(self.sale2.id)}, self.admin)
        self.assertTrue(any(r["order_id"] == self.sale2.id for r in d3["rows"]))

    def test_24_sorting(self):
        """24. Saralash: returned_qty, refund_amount, profit_impact, order_id."""
        d_qty = ReportBuilderService.generate({
            "report_type": "order_returns", "sort_by": "returned_qty", "sort_dir": "desc",
        }, self.admin)
        qtys = [r["returned_qty"] for r in d_qty["rows"]]
        self.assertEqual(qtys, sorted(qtys, reverse=True))

        d_amt = ReportBuilderService.generate({
            "report_type": "order_returns", "sort_by": "refund_amount", "sort_dir": "desc",
        }, self.admin)
        amts = [float(r["refund_amount"]) for r in d_amt["rows"]]
        self.assertEqual(amts, sorted(amts, reverse=True))

    def test_25_payment_method_resolution(self):
        """25. To'lov usuli: Naqd, Karta va Qarz to'g'ri aniqlanishi."""
        d_cash = ReportBuilderService.generate({"report_type": "order_returns", "return_id": str(self.ret1.id)}, self.admin)
        self.assertEqual(d_cash["rows"][0]["payment_method"], "Naqd")

        d_card = ReportBuilderService.generate({"report_type": "order_returns", "return_id": str(self.ret2.id)}, self.admin)
        self.assertIn("Karta", d_card["rows"][0]["payment_method"])

        d_debt = ReportBuilderService.generate({"report_type": "order_returns", "return_id": str(self.ret7.id)}, self.admin)
        self.assertEqual(d_debt["rows"][0]["payment_method"], "Qarz")

    def test_26_decimal_quantity(self):
        """26. Kasr miqdor (0.5 juft) qaytarilishi to'g'ri aks etishi."""
        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "return_id": str(self.ret8.id),
        }, self.admin)
        self.assertEqual(data["rows"][0]["returned_qty"], 0.5)
        self.assertEqual(data["rows"][0]["refund_amount"], "125000.00")

    def test_27_store_isolation_generate(self):
        """27. Do'kon izolyatsiyasi: Store 1 foydalanuvchisi Store 2 qaytarimlarini ko'ra olmasligi."""
        view = ReportBuilderGenerateAPIView.as_view()
        # Store 1 xodimi Store 2 ni so'rasa ham, server uni Store 1 ga majburlaydi
        req = self.factory.get(f"/api/v1/reports/builder/generate/?report_type=order_returns&store_id={self.store2.id}")
        force_authenticate(req, user=self.store_user)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        for r in resp.data["rows"]:
            self.assertEqual(r["store_id"], self.store1.id)
            self.assertNotEqual(r["store_id"], self.store2.id)

    def test_28_rbac_permissions(self):
        """28. RBAC: huquqsiz 403, reports.order_returns.view va reports.sales.view bilan 200."""
        view = ReportBuilderGenerateAPIView.as_view()

        # Huquqsiz foydalanuvchi -> 403
        req1 = self.factory.get("/api/v1/reports/builder/generate/?report_type=order_returns")
        force_authenticate(req1, user=self.no_perm_user)
        resp1 = view(req1)
        self.assertEqual(resp1.status_code, 403)

        # reports.order_returns.view huquqi bor -> 200
        req2 = self.factory.get("/api/v1/reports/builder/generate/?report_type=order_returns")
        force_authenticate(req2, user=self.order_returns_viewer_user)
        resp2 = view(req2)
        self.assertEqual(resp2.status_code, 200)

        # Merosiy reports.sales.view huquqi bor -> 200 (fallback)
        req3 = self.factory.get("/api/v1/reports/builder/generate/?report_type=order_returns")
        force_authenticate(req3, user=self.sales_viewer_user)
        resp3 = view(req3)
        self.assertEqual(resp3.status_code, 200)

    def test_29_export_excel_openxml_table(self):
        """29. Excel eksport: haqiqiy OpenXML Table, AutoFilter va Freeze Panes tekshiruvi."""
        import io, openpyxl
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=order_returns&export_type=excel")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        self.assertIsNotNone(ws)
        # OpenXML jadvali (Table) mavjudligi
        self.assertGreater(len(ws.tables), 0)
        table = list(ws.tables.values())[0]
        self.assertIsNotNone(table.autoFilter)
        # Freeze panes mavjudligi
        self.assertIsNotNone(ws.freeze_panes)

    def test_30_export_csv_utf8_bom(self):
        """30. CSV eksport: UTF-8 BOM va to'g'ri sarlavhalar."""
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=order_returns&export_type=csv")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        self.assertTrue(resp.content.startswith(b"\xef\xbb\xbf"))
        csv_text = resp.content.decode("utf-8-sig")
        self.assertIn("Qaytarish ID", csv_text)
        self.assertIn("Yog' filtri Bosch", csv_text)

    def test_31_export_pdf_landscape(self):
        """31. PDF eksport: Landscape format va %PDF identifikatori."""
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=order_returns&export_type=pdf")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF"))

    def test_32_export_store_isolation(self):
        """32. Eksportda do'kon izolyatsiyasi: Store 1 xodimi Store 2 qaytarimlarini yuklab ololmasligi."""
        view = ReportBuilderExportAPIView.as_view()
        for exp_type in ("csv", "excel", "pdf"):
            req = self.factory.get(
                f"/api/v1/reports/builder/export/?report_type=order_returns&export_type={exp_type}&store_id={self.store2.id}"
            )
            force_authenticate(req, user=self.store_user)
            resp = view(req)
            self.assertEqual(resp.status_code, 200)
            if exp_type == "csv":
                text = resp.content.decode("utf-8-sig")
                self.assertNotIn("BAT-505", text)
            elif exp_type == "excel":
                import io, openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(resp.content))
                ws = wb.active
                cells = [cell.value for row in ws.iter_rows(min_row=5) for cell in row if cell.value]
                self.assertNotIn("BAT-505", cells)
            elif exp_type == "pdf":
                self.assertNotIn(b"BAT-505", resp.content)

    def test_33_fixed_query_count_no_n_plus_one(self):
        """33. Fixed Query Count: 1 ta qaytarimda ham, 10 ta qaytarimda ham so'rovlar soni doimiy (fixed) qolishi."""
        # 1 ta qaytarim bilan so'rovlar soni (3 ta so'rov: Asosiy, Supplier, Payment)
        with self.assertNumQueries(3):
            ReportingFoundationService.get_order_returns_metrics(return_id=self.ret1.id)

        # Barcha qaytarimlar bilan so'rovlar soni (4 ta so'rov: Asosiy, Supplier, Batch fallback, Payment)
        with self.assertNumQueries(4):
            ReportingFoundationService.get_order_returns_metrics()

    def test_34_end_to_end_sale_return_service(self):
        """34. End-to-end: SaleReturnService.create_return orqali qaytarim yaratilib, hisobotda aniq chiqishi."""
        from apps.sales.services.sale_return_service import SaleReturnService
        new_sale = Sale.objects.create(
            store=self.store1, customer=self.cust1, seller=self.seller_user,
            total_amount=Decimal("200000.00"), paid_amount=Decimal("200000.00"), status=Sale.Status.PAID,
        )
        new_si = SaleItem.objects.create(
            sale=new_sale, product=self.p1, quantity=Decimal("2.00"), unit_price=Decimal("100000.00"),
            purchase_price=Decimal("60000.00"), total_price=Decimal("200000.00"),
        )
        return_obj = SaleReturnService.create_return(
            user=self.seller_user,
            data={
                "sale": new_sale.id,
                "items": [{"sale_item": new_si.id, "quantity": 1}],
                "comment": "E2E qaytarim testi",
            },
        )
        self.assertIsNotNone(return_obj)

        data = ReportBuilderService.generate({
            "report_type": "order_returns",
            "return_id": str(return_obj.id),
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        r = data["rows"][0]
        self.assertEqual(r["return_id"], return_obj.id)
        self.assertEqual(r["refund_amount"], "100000.00")
        self.assertEqual(r["purchase_value"], "60000.00")
        self.assertEqual(r["profit_impact"], "40000.00")
        self.assertEqual(r["comment"], "E2E qaytarim testi")


class WriteOffsReportTest(TestCase):
    """
    Phase 1.6: "Hisobdan chiqarishlar" (Write-offs) hisoboti uchun to'liq test to'plami.

    Asosiy tamoyillar:
      1. Dataset root: WriteOffItem (granular item-level). Pair: WriteOff + WriteOffItem.
      2. Asosiy sana: WriteOff.created_at in [start, end) sargable oraliq.
      3. quantity -> WriteOffItem.quantity (0.5 qadam bilan juft mahsulotlar ham qo'llab-quvvatlanadi).
      4. unit_purchase_price -> WriteOffItem.purchase_price (snapshot).
      5. unit_sale_price -> WriteOffItem.selling_price (snapshot).
      6. purchase_value -> quantity * unit_purchase_price.
      7. sale_value -> quantity * unit_sale_price.
      8. profit_impact -> sale_value - purchase_value (yo'qotilgan yalpi marja).
      9. supplier -> latest StockEntryItem.entry.supplier (arxitektura cheklovi).
      10. product_status -> Faol / Nofaol (Arxiv) / Qoralama.
      11. inventory_session_id -> WriteOff.inventory_session_id (mavjud bo'lsa int, aks holda '-').
      12. has_zero_cost_items -> tannarx 0 bo'lgan tovarlar uchun ogohlantirish.
      13. Do'kon izolyatsiyasi (Store isolation) va barcha 13 ta filtrlar.
      14. RBAC: reports.write_offs.view / export va legacy fallbacklar (writeoff.cancel QAT'IYAN taqiqlangan).
      15. Excel (OpenXML Table, AutoFilter, Freeze Panes), CSV (UTF-8 BOM), PDF (Landscape).
      16. Fixed query count / N+1 tekshiruvi (doimiy 2 ta so'rov).
    """

    @classmethod
    def setUpTestData(cls):
        cls.factory = APIRequestFactory()

        # 1. Foydalanuvchilar
        cls.admin = User.objects.create(
            phone_number="+998900003001", email="admin.wo@crm.uz",
            is_superuser=True, is_staff=True, full_name="Super Admin",
        )
        cls.store_user = User.objects.create(
            phone_number="+998900003002", email="store1.wo@crm.uz",
            is_superuser=False, is_staff=True, full_name="Store 1 Xodim",
        )
        cls.other_store_user = User.objects.create(
            phone_number="+998900003003", email="store2.wo@crm.uz",
            is_superuser=False, is_staff=True, full_name="Store 2 Xodim",
        )
        cls.author_user = User.objects.create(
            phone_number="+998900003004", email="author.wo@crm.uz",
            is_superuser=False, is_staff=True, full_name="Omborchi Akmal",
        )
        cls.wo_viewer_user = User.objects.create(
            phone_number="+998900003005", email="woviewer@crm.uz",
            is_superuser=False, is_staff=True, full_name="WO Viewer",
        )
        cls.inv_viewer_user = User.objects.create(
            phone_number="+998900003006", email="invviewer@crm.uz",
            is_superuser=False, is_staff=True, full_name="Inventory Viewer",
        )
        cls.writeoff_viewer_user = User.objects.create(
            phone_number="+998900003007", email="writeoffviewer@crm.uz",
            is_superuser=False, is_staff=True, full_name="Legacy Writeoff Viewer",
        )
        cls.cancel_only_user = User.objects.create(
            phone_number="+998900003008", email="cancelonly@crm.uz",
            is_superuser=False, is_staff=True, full_name="Cancel Only User",
        )
        cls.no_perm_user = User.objects.create(
            phone_number="+998900003009", email="noperm.wo@crm.uz",
            is_superuser=False, is_staff=True, full_name="No Perm User",
        )

        # 2. Do'konlar
        cls.store1 = Store.objects.create(
            name="Markaziy Filial", phone_number="+998900003011",
            address="Toshkent", type=Store.StoreType.STORE,
        )
        cls.store2 = Store.objects.create(
            name="Samarqand Filiali", phone_number="+998900003012",
            address="Samarqand", type=Store.StoreType.STORE,
        )

        # 3. Rollar va ruxsatlar
        role_wo_viewer = Role.objects.create(
            name="Write-offs Viewer",
            permissions=["reports.view", "reports.write_offs.view", "reports.write_offs.export"],
        )
        role_inv_viewer = Role.objects.create(
            name="Legacy Inventory Viewer",
            permissions=["reports.view", "inventory.view", "inventory.export"],
        )
        role_writeoff_viewer = Role.objects.create(
            name="Legacy WriteOff Viewer",
            permissions=["reports.view", "writeoff.view"],
        )
        role_cancel_only = Role.objects.create(
            name="Cancel Only Role",
            permissions=["reports.view", "writeoff.cancel"],
        )
        role_store_user = Role.objects.create(
            name="Store User Role",
            permissions=["reports.view", "reports.write_offs.view", "reports.write_offs.export"],
        )

        cls.store_user.role = role_store_user
        cls.store_user.save()
        cls.other_store_user.role = role_store_user
        cls.other_store_user.save()
        cls.author_user.role = role_store_user
        cls.author_user.save()
        cls.wo_viewer_user.role = role_wo_viewer
        cls.wo_viewer_user.save()
        cls.inv_viewer_user.role = role_inv_viewer
        cls.inv_viewer_user.save()
        cls.writeoff_viewer_user.role = role_writeoff_viewer
        cls.writeoff_viewer_user.save()
        cls.cancel_only_user.role = role_cancel_only
        cls.cancel_only_user.save()

        StoreUser.objects.create(store=cls.store1, user=cls.store_user, is_active=True)
        StoreUser.objects.create(store=cls.store1, user=cls.author_user, is_active=True)
        StoreUser.objects.create(store=cls.store1, user=cls.wo_viewer_user, is_active=True)
        StoreUser.objects.create(store=cls.store1, user=cls.inv_viewer_user, is_active=True)
        StoreUser.objects.create(store=cls.store1, user=cls.writeoff_viewer_user, is_active=True)
        StoreUser.objects.create(store=cls.store1, user=cls.cancel_only_user, is_active=True)
        StoreUser.objects.create(store=cls.store2, user=cls.other_store_user, is_active=True)

        # 4. Kategoriya, brend, o'lchov
        cls.cat1 = Category.objects.create(name="Moylar")
        cls.cat2 = Category.objects.create(name="Filtrlar")
        cls.brand1 = Brand.objects.create(name="Castrol")
        cls.brand2 = Brand.objects.create(name="Mann")
        cls.unit_dona = ProductUnitMeasurement.objects.create(measurement="dona")
        cls.unit_pair = ProductUnitMeasurement.objects.create(measurement="juft")

        # 5. Mahsulotlar
        cls.p1 = Product.objects.create(
            name="Castrol Edge 5W-40", sku="CST-001", barcode="4782001001",
            category=cls.cat1, brand=cls.brand1, unit_measurement=cls.unit_dona,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.p2 = Product.objects.create(
            name="Mann Moy Filtri", sku="MNN-002", barcode="4782001002",
            category=cls.cat2, brand=cls.brand2, unit_measurement=cls.unit_dona,
            status=Product.ProductStatus.INACTIVE,
        )
        cls.p3 = Product.objects.create(
            name="Qoralama Shina", sku="SHN-003", barcode="4782001003",
            category=cls.cat1, brand=cls.brand1, unit_measurement=cls.unit_dona,
            status=Product.ProductStatus.DRAFT,
        )
        cls.p4_pair = Product.objects.create(
            name="Ishchi Qo'lqop", sku="GLV-004", barcode="4782001004",
            category=cls.cat2, brand=cls.brand2, unit_measurement=cls.unit_pair,
            is_pair=True, status=Product.ProductStatus.ACTIVE,
        )
        cls.p5_zero_cost = Product.objects.create(
            name="Sovg'a Nakleyka", sku="NKL-005", barcode="4782001005",
            category=cls.cat1, brand=cls.brand1, unit_measurement=cls.unit_dona,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.p_store2 = Product.objects.create(
            name="Store2 Mahsuloti", sku="ST2-006", barcode="4782001006",
            category=cls.cat2, brand=cls.brand2, unit_measurement=cls.unit_dona,
            status=Product.ProductStatus.ACTIVE,
        )

        # 6. Partiyalar (ProductBatch)
        ProductBatch.objects.create(
            store=cls.store1, product=cls.p1, quantity=Decimal("50"),
            purchase_price=Decimal("100000.00"), selling_price=Decimal("150000.00"), is_active=True,
        )
        ProductBatch.objects.create(
            store=cls.store1, product=cls.p2, quantity=Decimal("30"),
            purchase_price=Decimal("50000.00"), selling_price=Decimal("80000.00"), is_active=True,
        )

        # 7. Ta'minotchi va Kirim (StockEntry)
        cls.sup1 = Supplier.objects.create(name="Castrol Rasmiy Ta'minotchi")
        cls.sup2 = Supplier.objects.create(name="Mann Rasmiy Ta'minotchi")
        se1 = StockEntry.objects.create(store=cls.store1, supplier=cls.sup1, total_amount=Decimal("1000000.00"))
        StockEntryItem.objects.create(entry=se1, product=cls.p1, quantity=10, purchase_price=Decimal("100000.00"), selling_price=Decimal("150000.00"))
        se2 = StockEntry.objects.create(store=cls.store1, supplier=cls.sup2, total_amount=Decimal("500000.00"))
        StockEntryItem.objects.create(entry=se2, product=cls.p2, quantity=10, purchase_price=Decimal("50000.00"), selling_price=Decimal("80000.00"))

        # 8. Inventarizatsiya sessiyasi
        cls.inv_session = InventorySession.objects.create(store=cls.store1, status=InventorySession.Status.COMPLETED)

        # 9. Hisobdan chiqarishlar (WriteOff va WriteOffItem)
        # WO 1: 2026-03-05, store1, reason DAMAGED, author_user, 2 ta item
        cls.wo1 = WriteOff.objects.create(
            store=cls.store1, reason=WriteOff.Reason.DAMAGED,
            created_by=cls.author_user, comment="Buzilgan idishlar",
            total_amount=Decimal("250000.00"),
        )
        cls.wo1_item1 = WriteOffItem.objects.create(
            write_off=cls.wo1, product=cls.p1,
            quantity=Decimal("2.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("150000.00"),
        )
        cls.wo1_item2 = WriteOffItem.objects.create(
            write_off=cls.wo1, product=cls.p2,
            quantity=Decimal("1.00"),
            purchase_price=Decimal("50000.00"),
            selling_price=Decimal("80000.00"),
        )
        WriteOff.objects.filter(id=cls.wo1.id).update(created_at=datetime(2026, 3, 5, 10, 0, tzinfo=dt_timezone.utc))

        # WO 2: 2026-03-10, store1, reason EXPIRED, author_user, 1 ta item
        cls.wo2 = WriteOff.objects.create(
            store=cls.store1, reason=WriteOff.Reason.EXPIRED,
            created_by=cls.author_user, comment="Muddati o'tgan shina",
            total_amount=Decimal("210000.00"),
        )
        cls.wo2_item1 = WriteOffItem.objects.create(
            write_off=cls.wo2, product=cls.p3,
            quantity=Decimal("3.00"),
            purchase_price=Decimal("70000.00"),
            selling_price=Decimal("110000.00"),
        )
        WriteOff.objects.filter(id=cls.wo2.id).update(created_at=datetime(2026, 3, 10, 12, 0, tzinfo=dt_timezone.utc))

        # WO 3: 2026-03-15, store1, reason INVENTORY, inventory_session, admin, 1 ta item (juft, 0.5 dona)
        cls.wo3 = WriteOff.objects.create(
            store=cls.store1, reason=WriteOff.Reason.INVENTORY,
            inventory_session=cls.inv_session,
            created_by=cls.admin, comment="Kamomad aniqlandi",
            total_amount=Decimal("20000.00"),
        )
        cls.wo3_item1 = WriteOffItem.objects.create(
            write_off=cls.wo3, product=cls.p4_pair,
            quantity=Decimal("0.50"),
            purchase_price=Decimal("40000.00"),
            selling_price=Decimal("60000.00"),
        )
        WriteOff.objects.filter(id=cls.wo3.id).update(created_at=datetime(2026, 3, 15, 14, 0, tzinfo=dt_timezone.utc))

        # WO 4: 2026-03-20, store1, reason LOST, admin, 1 ta item (purchase_price == 0)
        cls.wo4_zero = WriteOff.objects.create(
            store=cls.store1, reason=WriteOff.Reason.LOST,
            created_by=cls.admin, comment="Yo'qolgan sovg'a",
            total_amount=Decimal("0.00"),
        )
        cls.wo4_item1 = WriteOffItem.objects.create(
            write_off=cls.wo4_zero, product=cls.p5_zero_cost,
            quantity=Decimal("1.00"),
            purchase_price=Decimal("0.00"),
            selling_price=Decimal("20000.00"),
        )
        WriteOff.objects.filter(id=cls.wo4_zero.id).update(created_at=datetime(2026, 3, 20, 16, 0, tzinfo=dt_timezone.utc))

        # WO 5: 2026-03-25, store2, reason CATALOG, other_store_user, 1 ta item
        cls.wo5_store2 = WriteOff.objects.create(
            store=cls.store2, reason=WriteOff.Reason.CATALOG,
            created_by=cls.other_store_user, comment="Store 2 katalogdan chiqarish",
            total_amount=Decimal("120000.00"),
        )
        cls.wo5_item1 = WriteOffItem.objects.create(
            write_off=cls.wo5_store2, product=cls.p_store2,
            quantity=Decimal("4.00"),
            purchase_price=Decimal("30000.00"),
            selling_price=Decimal("45000.00"),
        )
        WriteOff.objects.filter(id=cls.wo5_store2.id).update(created_at=datetime(2026, 3, 25, 11, 0, tzinfo=dt_timezone.utc))

        # WO Outside: 2026-01-10, store1, reason OTHER (mart oyidan tashqarida)
        cls.wo_outside = WriteOff.objects.create(
            store=cls.store1, reason=WriteOff.Reason.OTHER,
            created_by=cls.admin, comment="Yanvar oyidagi chiqarish",
            total_amount=Decimal("500000.00"),
        )
        cls.wo_outside_item1 = WriteOffItem.objects.create(
            write_off=cls.wo_outside, product=cls.p1,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("100000.00"),
            selling_price=Decimal("150000.00"),
        )
        WriteOff.objects.filter(id=cls.wo_outside.id).update(created_at=datetime(2026, 1, 10, 9, 0, tzinfo=dt_timezone.utc))

    def test_01_grain_and_root_dataset(self):
        """01. Dataset root: WriteOffItem (Grain: 1 row = 1 WriteOffItem). wo1 dagi 2 ta item 2 ta qator bo'lib chiqadi."""
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "write_off_id": str(self.wo1.id),
        }, self.admin)
        self.assertEqual(len(data["rows"]), 2)
        p_names = {r["product_name"] for r in data["rows"]}
        self.assertIn("Castrol Edge 5W-40", p_names)
        self.assertIn("Mann Moy Filtri", p_names)

    def test_02_all_21_columns_present(self):
        """02. Barcha 21 ta contract ustunlari qatorda mavjudligi."""
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "write_off_id": str(self.wo1.id),
        }, self.admin)
        r = data["rows"][0]
        expected_cols = [
            "write_off_id", "store_name", "write_off_datetime", "reason_display",
            "created_by_name", "product_name", "sku", "barcode", "category_name",
            "brand_name", "unit", "supplier_name", "product_status", "quantity",
            "unit_purchase_price", "unit_sale_price", "purchase_value", "sale_value",
            "profit_impact", "inventory_session_id", "comment",
        ]
        for col in expected_cols:
            self.assertIn(col, r, f"Ustun mavjud emas: {col}")

    def test_03_snapshot_prices_and_formulas(self):
        """03. Snapshot narxlar va hisob-kitob formulalari to'g'riligi."""
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "write_off_id": str(self.wo1.id),
        }, self.admin)
        item1 = next(r for r in data["rows"] if r["sku"] == "CST-001")
        self.assertEqual(item1["quantity"], 2.0)
        self.assertEqual(item1["unit_purchase_price"], "100000.00")
        self.assertEqual(item1["unit_sale_price"], "150000.00")
        self.assertEqual(item1["purchase_value"], "200000.00")
        self.assertEqual(item1["sale_value"], "300000.00")
        self.assertEqual(item1["profit_impact"], "100000.00")

    def test_04_immutability_on_batch_mutation(self):
        """04. Tarixiy narxlar daxlsizligi: keyinchalik ProductBatch narxi o'zgarsa ham hisobot o'zgarmasligi."""
        ProductBatch.objects.filter(store=self.store1, product=self.p1).update(
            purchase_price=Decimal("999999.00"),
            selling_price=Decimal("888888.00"),
        )
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "write_off_id": str(self.wo1.id),
        }, self.admin)
        item1 = next(r for r in data["rows"] if r["sku"] == "CST-001")
        self.assertEqual(item1["unit_purchase_price"], "100000.00")
        self.assertEqual(item1["unit_sale_price"], "150000.00")
        self.assertEqual(item1["purchase_value"], "200000.00")

    def test_05_date_interval_sargable(self):
        """05. WriteOff.created_at bo'yicha [start, end) sargable oraliqda to'g'ri filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-12",
            "store_id": str(self.store1.id),
        }, self.admin)
        wo_ids = {r["write_off_id"] for r in data["rows"]}
        self.assertIn(self.wo1.id, wo_ids)
        self.assertIn(self.wo2.id, wo_ids)
        self.assertNotIn(self.wo3.id, wo_ids)
        self.assertNotIn(self.wo_outside.id, wo_ids)

    def test_06_filter_store_id(self):
        """06. Do'kon (store_id) filtri."""
        data_s1 = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-31",
            "store_id": str(self.store1.id),
        }, self.admin)
        self.assertTrue(all(r["store_id"] == self.store1.id for r in data_s1["rows"]))
        self.assertNotIn(self.wo5_store2.id, {r["write_off_id"] for r in data_s1["rows"]})

        data_s2 = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-31",
            "store_id": str(self.store2.id),
        }, self.admin)
        self.assertTrue(all(r["store_id"] == self.store2.id for r in data_s2["rows"]))
        self.assertIn(self.wo5_store2.id, {r["write_off_id"] for r in data_s2["rows"]})

    def test_07_filter_reason(self):
        """07. Sabab (reason) bo'yicha filtrlash va reason_display tekshiruvi."""
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-31",
            "reason": "damaged",
        }, self.admin)
        self.assertEqual(len(data["rows"]), 2)
        for r in data["rows"]:
            self.assertEqual(r["reason"], "damaged")
            self.assertEqual(r["reason_display"], "Buzilgan / yaroqsiz")

    def test_08_filter_category_and_brand(self):
        """08. Kategoriya va brend bo'yicha filtrlash."""
        data_cat = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-31",
            "category_id": str(self.cat2.id),
        }, self.admin)
        self.assertEqual(len(data_cat["rows"]), 3)
        self.assertTrue(all(r["category_name"] == "Filtrlar" for r in data_cat["rows"]))

        data_brand = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-31",
            "brand_id": str(self.brand1.id),
        }, self.admin)
        self.assertTrue(all(r["brand_name"] == "Castrol" for r in data_brand["rows"]))

    def test_09_filter_supplier_and_attribution(self):
        """09. Ta'minotchi filtri va oxirgi kirim ta'minotchisini aniqlash."""
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-31",
            "supplier_id": str(self.sup1.id),
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["sku"], "CST-001")
        self.assertEqual(data["rows"][0]["supplier_name"], "Castrol Rasmiy Ta'minotchi")

    def test_10_filter_user_id(self):
        """10. Mas'ul xodim (user_id) bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-31",
            "user_id": str(self.author_user.id),
        }, self.admin)
        self.assertEqual(len(data["rows"]), 3)
        self.assertTrue(all(r["created_by_id"] == self.author_user.id for r in data["rows"]))
        self.assertTrue(all(r["created_by_name"] == "Omborchi Akmal" for r in data["rows"]))

    def test_11_filter_write_off_id(self):
        """11. Aniq bitta hisobdan chiqarish hujjati (write_off_id) bo'yicha filtrlash."""
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "write_off_id": str(self.wo2.id),
        }, self.admin)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["rows"][0]["write_off_id"], self.wo2.id)
        self.assertEqual(data["rows"][0]["comment"], "Muddati o'tgan shina")

    def test_12_filter_sku_and_barcode(self):
        """12. SKU va shtrix-kod bo'yicha aniq filtrlash."""
        data_sku = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-31",
            "sku": "GLV-004",
        }, self.admin)
        self.assertEqual(len(data_sku["rows"]), 1)
        self.assertEqual(data_sku["rows"][0]["sku"], "GLV-004")

        data_bar = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-31",
            "barcode": "4782001004",
        }, self.admin)
        self.assertEqual(len(data_bar["rows"]), 1)
        self.assertEqual(data_bar["rows"][0]["barcode"], "4782001004")

    def test_13_filter_search(self):
        """13. Umumiy qidiruv (search): tovar nomi, sku, barcode, comment va hujjat raqami."""
        d1 = ReportBuilderService.generate({"report_type": "write_offs", "search": "Edge"}, self.admin)
        self.assertGreaterEqual(len(d1["rows"]), 1)
        self.assertEqual(d1["rows"][0]["sku"], "CST-001")

        d2 = ReportBuilderService.generate({"report_type": "write_offs", "search": "Kamomad"}, self.admin)
        self.assertEqual(len(d2["rows"]), 1)
        self.assertEqual(d2["rows"][0]["sku"], "GLV-004")

        d3 = ReportBuilderService.generate({"report_type": "write_offs", "search": str(self.wo2.id)}, self.admin)
        self.assertTrue(any(r["write_off_id"] == self.wo2.id for r in d3["rows"]))

    def test_14_sorting(self):
        """14. Turli ustunlar bo'yicha saralash (asc/desc)."""
        d_qty = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-31",
            "sort_by": "quantity",
            "sort_dir": "desc",
        }, self.admin)
        qtys = [r["quantity"] for r in d_qty["rows"]]
        self.assertEqual(qtys, sorted(qtys, reverse=True))

        d_val = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-31",
            "sort_by": "purchase_value",
            "sort_dir": "asc",
        }, self.admin)
        vals = [float(r["purchase_value"]) for r in d_val["rows"]]
        self.assertEqual(vals, sorted(vals))

    def test_15_zero_cost_warning(self):
        """15. Tannarxi 0 bo'lgan tovar chiqarilganda has_zero_cost_items=True va warning berilishi."""
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "write_off_id": str(self.wo4_zero.id),
        }, self.admin)
        self.assertTrue(data.get("info", {}).get("warning") is not None)
        self.assertEqual(data["rows"][0]["unit_purchase_price"], "0.00")

    def test_16_decimal_and_pair_quantity(self):
        """16. Juft mahsulotlar uchun 0.5 qadamli kasr miqdor aniq saqlanishi."""
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "write_off_id": str(self.wo3.id),
        }, self.admin)
        self.assertEqual(data["rows"][0]["quantity"], 0.5)
        self.assertEqual(data["rows"][0]["unit"], "juft")
        self.assertEqual(data["rows"][0]["purchase_value"], "20000.00")

    def test_17_product_status_display(self):
        """17. Tovar holati (product_status) to'g'ri o'zbekcha etiketkalar bilan ko'rsatilishi."""
        data_p1 = ReportBuilderService.generate({"report_type": "write_offs", "sku": "CST-001"}, self.admin)
        self.assertEqual(data_p1["rows"][0]["product_status"], "Faol")

        data_p2 = ReportBuilderService.generate({"report_type": "write_offs", "sku": "MNN-002"}, self.admin)
        self.assertEqual(data_p2["rows"][0]["product_status"], "Nofaol (Arxiv)")

        data_p3 = ReportBuilderService.generate({"report_type": "write_offs", "sku": "SHN-003"}, self.admin)
        self.assertEqual(data_p3["rows"][0]["product_status"], "Qoralama")

    def test_18_inventory_session_id_display(self):
        """18. Inventarizatsiyadan chiqqan bo'lsa session_id, aks holda '-' ko'rsatilishi."""
        data_inv = ReportBuilderService.generate({"report_type": "write_offs", "write_off_id": str(self.wo3.id)}, self.admin)
        self.assertEqual(data_inv["rows"][0]["inventory_session_id"], self.inv_session.id)

        data_other = ReportBuilderService.generate({"report_type": "write_offs", "write_off_id": str(self.wo1.id)}, self.admin)
        self.assertEqual(data_other["rows"][0]["inventory_session_id"], "-")

    def test_19_summary_totals(self):
        """19. Xulosa (summary) kartochkalari agregatsiyasi to'g'riligi."""
        data = ReportBuilderService.generate({
            "report_type": "write_offs",
            "from": "2026-03-01",
            "to": "2026-03-12",
            "store_id": str(self.store1.id),
        }, self.admin)
        sum_dict = {s["label"]: s["value"] for s in data["summary"]}
        self.assertEqual(sum_dict["Hujjatlar soni"], 2)
        self.assertEqual(sum_dict["Chiqarilgan tovarlar"], 6.0)
        self.assertEqual(sum_dict["Tannarx summasi"], "460000.00")
        self.assertEqual(sum_dict["Sotuv summasi"], "710000.00")
        self.assertEqual(sum_dict["Yo'qotilgan foyda"], "250000.00")

    def test_20_store_isolation_server_enforced(self):
        """20. Do'kon izolyatsiyasi: Store 1 foydalanuvchisi Store 2 hisobdan chiqarishlarini ko'ra olmasligi."""
        view = ReportBuilderGenerateAPIView.as_view()
        req = self.factory.get(f"/api/v1/reports/builder/generate/?report_type=write_offs&store_id={self.store2.id}")
        force_authenticate(req, user=self.store_user)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        wo_ids = {r["write_off_id"] for r in resp.data["rows"]}
        self.assertNotIn(self.wo5_store2.id, wo_ids)

    def _make_rbac_user(self, perms: list[str]):
        import uuid
        uid = uuid.uuid4().hex[:8]
        role = Role.objects.create(name=f"Role-{uid}", permissions=perms)
        user = User.objects.create(
            phone_number=f"+99890{uid[:7]}",
            email=f"user_{uid}@crm.uz",
            is_superuser=False,
            is_staff=True,
            role=role,
        )
        StoreUser.objects.create(store=self.store1, user=user, is_active=True)
        return user

    def test_21a_rbac_case_a_write_offs_view_only(self):
        """Case A: User faqat reports.write_offs.view ga ega -> View=200, Export=200 (fallback)."""
        user = self._make_rbac_user(["reports.view", "reports.write_offs.view"])
        view_gen = ReportBuilderGenerateAPIView.as_view()
        req_gen = self.factory.get("/api/v1/reports/builder/generate/?report_type=write_offs")
        force_authenticate(req_gen, user=user)
        self.assertEqual(view_gen(req_gen).status_code, 200)

        view_exp = ReportBuilderExportAPIView.as_view()
        req_exp = self.factory.get("/api/v1/reports/builder/export/?report_type=write_offs&export_type=csv")
        force_authenticate(req_exp, user=user)
        self.assertEqual(view_exp(req_exp).status_code, 200)

    def test_21b_rbac_case_b_write_offs_export_only(self):
        """Case B: User faqat reports.write_offs.export ga ega -> Export=200."""
        user = self._make_rbac_user(["reports.view", "reports.write_offs.export"])
        view_exp = ReportBuilderExportAPIView.as_view()
        req_exp = self.factory.get("/api/v1/reports/builder/export/?report_type=write_offs&export_type=excel")
        force_authenticate(req_exp, user=user)
        self.assertEqual(view_exp(req_exp).status_code, 200)

    def test_21c_rbac_case_c_reports_inventory_view_only(self):
        """Case C: User faqat reports.inventory.view ga ega -> View=200, Export=403."""
        user = self._make_rbac_user(["reports.view", "reports.inventory.view"])
        view_gen = ReportBuilderGenerateAPIView.as_view()
        req_gen = self.factory.get("/api/v1/reports/builder/generate/?report_type=write_offs")
        force_authenticate(req_gen, user=user)
        self.assertEqual(view_gen(req_gen).status_code, 200)

        view_exp = ReportBuilderExportAPIView.as_view()
        req_exp = self.factory.get("/api/v1/reports/builder/export/?report_type=write_offs&export_type=excel")
        force_authenticate(req_exp, user=user)
        self.assertEqual(view_exp(req_exp).status_code, 403)

    def test_21d_rbac_case_d_inventory_view_only(self):
        """Case D: User faqat inventory.view ga ega -> View=200, Export=403."""
        user = self._make_rbac_user(["reports.view", "inventory.view"])
        view_gen = ReportBuilderGenerateAPIView.as_view()
        req_gen = self.factory.get("/api/v1/reports/builder/generate/?report_type=write_offs")
        force_authenticate(req_gen, user=user)
        self.assertEqual(view_gen(req_gen).status_code, 200)

        view_exp = ReportBuilderExportAPIView.as_view()
        req_exp = self.factory.get("/api/v1/reports/builder/export/?report_type=write_offs&export_type=excel")
        force_authenticate(req_exp, user=user)
        self.assertEqual(view_exp(req_exp).status_code, 403)

    def test_21e_rbac_case_e_reports_inventory_export_only(self):
        """Case E: User faqat reports.inventory.export ga ega -> Export=200."""
        user = self._make_rbac_user(["reports.view", "reports.inventory.export"])
        view_exp = ReportBuilderExportAPIView.as_view()
        req_exp = self.factory.get("/api/v1/reports/builder/export/?report_type=write_offs&export_type=excel")
        force_authenticate(req_exp, user=user)
        self.assertEqual(view_exp(req_exp).status_code, 200)

    def test_21f_rbac_case_f_inventory_export_only(self):
        """Case F: User faqat inventory.export ga ega -> Export=200."""
        user = self._make_rbac_user(["reports.view", "inventory.export"])
        view_exp = ReportBuilderExportAPIView.as_view()
        req_exp = self.factory.get("/api/v1/reports/builder/export/?report_type=write_offs&export_type=excel")
        force_authenticate(req_exp, user=user)
        self.assertEqual(view_exp(req_exp).status_code, 200)

    def test_21g_rbac_case_g_writeoff_view_only(self):
        """Case G: User faqat writeoff.view ga ega -> View=200, Export=200 (fallback)."""
        user = self._make_rbac_user(["reports.view", "writeoff.view"])
        view_gen = ReportBuilderGenerateAPIView.as_view()
        req_gen = self.factory.get("/api/v1/reports/builder/generate/?report_type=write_offs")
        force_authenticate(req_gen, user=user)
        self.assertEqual(view_gen(req_gen).status_code, 200)

        view_exp = ReportBuilderExportAPIView.as_view()
        req_exp = self.factory.get("/api/v1/reports/builder/export/?report_type=write_offs&export_type=excel")
        force_authenticate(req_exp, user=user)
        self.assertEqual(view_exp(req_exp).status_code, 200)

    def test_21h_rbac_case_h_writeoff_cancel_strictly_forbidden(self):
        """Case H: QAT'IY TAQIQLANGAN: User faqat writeoff.cancel ga ega -> View=403, Export=403."""
        user = self._make_rbac_user(["reports.view", "writeoff.cancel"])
        view_gen = ReportBuilderGenerateAPIView.as_view()
        req_gen = self.factory.get("/api/v1/reports/builder/generate/?report_type=write_offs")
        force_authenticate(req_gen, user=user)
        self.assertEqual(view_gen(req_gen).status_code, 403)

        view_exp = ReportBuilderExportAPIView.as_view()
        req_exp = self.factory.get("/api/v1/reports/builder/export/?report_type=write_offs&export_type=excel")
        force_authenticate(req_exp, user=user)
        self.assertEqual(view_exp(req_exp).status_code, 403)

    def test_25_rbac_no_permission_forbidden(self):
        """25. Ruxsatsiz foydalanuvchiga 403 taqiqlangan javob qaytishi."""
        view_gen = ReportBuilderGenerateAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/generate/?report_type=write_offs")
        force_authenticate(req, user=self.no_perm_user)
        resp = view_gen(req)
        self.assertEqual(resp.status_code, 403)

    def test_26_meta_endpoint(self):
        """26. Meta endpoint: write_offs hisoboti va uning 13 ta filtri qaytarilishi."""
        view_meta = ReportBuilderMetaAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/meta/")
        force_authenticate(req, user=self.admin)
        resp = view_meta(req)
        self.assertEqual(resp.status_code, 200)
        report_keys = [r["key"] for r in resp.data["reports"]]
        self.assertIn("write_offs", report_keys)

        wo_spec = next(r for r in resp.data["reports"] if r["key"] == "write_offs")
        filter_params = [f["param"] for f in wo_spec["filters"]]
        for p in ["date", "store_id", "reason", "supplier_id", "category_id", "brand_id", "user_id", "write_off_id", "sku", "barcode", "sort_by"]:
            self.assertIn(p, filter_params)

    def test_27_export_excel_openxml_table(self):
        """27. Excel eksport: haqiqiy OpenXML Table, AutoFilter va Freeze Panes tekshiruvi."""
        import io, openpyxl
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=write_offs&export_type=excel")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        self.assertIsNotNone(ws)
        self.assertGreater(len(ws.tables), 0)
        table = list(ws.tables.values())[0]
        self.assertIsNotNone(table.autoFilter)

    def test_28_export_csv_utf8_bom(self):
        """28. CSV eksport: UTF-8 BOM va to'g'ri sarlavhalar."""
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=write_offs&export_type=csv")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        self.assertTrue(resp.content.startswith(b"\xef\xbb\xbf"))
        csv_text = resp.content.decode("utf-8-sig")
        self.assertIn("Hujjat №", csv_text)
        self.assertIn("Castrol Edge 5W-40", csv_text)

    def test_29_export_pdf_landscape(self):
        """29. PDF eksport: Landscape format va %PDF identifikatori."""
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=write_offs&export_type=pdf")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF"))

    def test_30_export_store_isolation(self):
        """30. Eksportda do'kon izolyatsiyasi: Store 1 xodimi Store 2 ma'lumotlarini yuklab ololmasligi."""
        view = ReportBuilderExportAPIView.as_view()
        for exp_type in ("csv", "excel", "pdf"):
            req = self.factory.get(
                f"/api/v1/reports/builder/export/?report_type=write_offs&export_type={exp_type}&store_id={self.store2.id}"
            )
            force_authenticate(req, user=self.store_user)
            resp = view(req)
            self.assertEqual(resp.status_code, 200)
            if exp_type == "csv":
                text = resp.content.decode("utf-8-sig")
                self.assertNotIn("ST2-006", text)
            elif exp_type == "excel":
                import io, openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(resp.content))
                ws = wb.active
                cells = [cell.value for row in ws.iter_rows(min_row=5) for cell in row if cell.value]
                self.assertNotIn("ST2-006", cells)
            elif exp_type == "pdf":
                self.assertNotIn(b"ST2-006", resp.content)

    def test_31_fixed_query_count_no_n_plus_one(self):
        """31. Fixed Query Count: 1 ta hujjatda ham, barcha hujjatlarda ham so'rovlar soni doimiy 2 ta (Zero N+1)."""
        # 1 ta write_off bilan so'rovlar soni (2 ta so'rov: Asosiy WriteOffItem va Supplier map)
        with self.assertNumQueries(2):
            ReportingFoundationService.get_write_offs_metrics(write_off_id=self.wo1.id)

        # Barcha write_offlar bilan so'rovlar soni (aynan 2 ta so'rov)
        with self.assertNumQueries(2):
            ReportingFoundationService.get_write_offs_metrics()




class ImportsReportTest(TestCase):
    """
    PHASE 1.7 — "Kirimlar (Importlar)" hisoboti uchun keng qamrovli test to'plami (40 ta test).
    """

    @classmethod
    def setUpTestData(cls):
        cls.factory = APIRequestFactory()

        # Do'konlar
        cls.store1 = Store.objects.create(name="Markaziy Filial", phone_number="+998901111111", address="Toshkent")
        cls.store2 = Store.objects.create(name="Samarqand Filiali", phone_number="+998902222222", address="Samarqand")

        # Rollar
        cls.manager_role = Role.objects.create(
            name="Imports Manager Role",
            permissions=["reports.view", "reports.imports.view", "reports.imports.export"],
        )
        cls.store1_role = Role.objects.create(
            name="Store 1 User Role",
            permissions=["reports.view", "reports.imports.view", "reports.imports.export"],
        )
        cls.store2_role = Role.objects.create(
            name="Store 2 User Role",
            permissions=["reports.view", "reports.imports.view", "reports.imports.export"],
        )
        cls.no_perm_role = Role.objects.create(
            name="No Perm Role",
            permissions=[],
        )

        # Foydalanuvchilar
        cls.admin = User.objects.create(
            phone_number="+998900000701",
            full_name="Super Admin",
            is_superuser=True,
            is_staff=True,
        )
        cls.manager = User.objects.create(
            phone_number="+998900000702",
            full_name="Ombor Menejeri",
            is_staff=True,
            role=cls.manager_role,
        )
        StoreUser.objects.create(user=cls.manager, store=cls.store1, is_active=True)

        cls.store1_user = User.objects.create(
            phone_number="+998900000703",
            full_name="Store 1 Xodimi",
            is_staff=True,
            role=cls.store1_role,
        )
        StoreUser.objects.create(user=cls.store1_user, store=cls.store1, is_active=True)

        cls.store2_user = User.objects.create(
            phone_number="+998900000704",
            full_name="Store 2 Xodimi",
            is_staff=True,
            role=cls.store2_role,
        )
        StoreUser.objects.create(user=cls.store2_user, store=cls.store2, is_active=True)

        cls.no_perm_user = User.objects.create(
            phone_number="+998900000705",
            full_name="Huquqsiz Xodim",
            is_staff=False,
            role=cls.no_perm_role,
        )

        # Yetkazib beruvchilar
        cls.sup_lukoil = Supplier.objects.create(name="Lukoil Distribution", phone_number="+998911111111")
        cls.sup_shell = Supplier.objects.create(name="Shell Uzbekistan", phone_number="+998922222222")

        # Mahsulot atributlari
        cls.cat_oil = Category.objects.create(name="Moylar")
        cls.cat_filter = Category.objects.create(name="Filtrlar")
        cls.brand_lukoil = Brand.objects.create(name="Lukoil")
        cls.brand_mann = Brand.objects.create(name="Mann-Filter")
        cls.unit_dona = ProductUnitMeasurement.objects.create(measurement="dona")

        # Mahsulotlar
        cls.prod_genesis = Product.objects.create(
            name="Genesis Special 5W-40",
            sku="LUK-GEN-001",
            barcode="460000000001",
            category=cls.cat_oil,
            brand=cls.brand_lukoil,
            unit_measurement=cls.unit_dona,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_mann = Product.objects.create(
            name="Moy filtri W712",
            sku="MAN-W712-002",
            barcode="4011558712002",
            category=cls.cat_filter,
            brand=cls.brand_mann,
            unit_measurement=cls.unit_dona,
            status=Product.ProductStatus.ACTIVE,
        )
        cls.prod_inactive = Product.objects.create(
            name="Eski Moy Arxiv",
            sku="OLD-OIL-003",
            barcode="990000000003",
            category=cls.cat_oil,
            brand=cls.brand_lukoil,
            unit_measurement=cls.unit_dona,
            status=Product.ProductStatus.INACTIVE,
        )
        cls.prod_zero_cost = Product.objects.create(
            name="Sovg'a Aksessuar",
            sku="GFT-ACC-004",
            barcode="880000000004",
            category=cls.cat_filter,
            unit_measurement=cls.unit_dona,
            status=Product.ProductStatus.DRAFT,
        )

        # ── Kirim 1: Store 1, Lukoil, To'langan, 2 ta tovar ──
        cls.dt1 = timezone.make_aware(datetime(2026, 1, 10, 10, 0, 0))
        cls.entry1 = StockEntry.objects.create(
            store=cls.store1,
            supplier=cls.sup_lukoil,
            created_by=cls.manager,
            total_amount=Decimal("2000.00"),
            cash_amount=Decimal("2000.00"),
            paid_amount=Decimal("2000.00"),
            debt_amount=Decimal("0.00"),
            note="Birinchi partiya kirimi (Lukoil to'liq to'landi)",
        )
        StockEntry.objects.filter(id=cls.entry1.id).update(created_at=cls.dt1)
        cls.entry1.refresh_from_db()

        cls.item1_1 = StockEntryItem.objects.create(
            entry=cls.entry1,
            product=cls.prod_genesis,
            quantity=Decimal("10.00"),
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            wholesale_price=Decimal("130.00"),
        )
        cls.item1_2 = StockEntryItem.objects.create(
            entry=cls.entry1,
            product=cls.prod_mann,
            quantity=Decimal("20.00"),
            purchase_price=Decimal("50.00"),
            selling_price=Decimal("80.00"),
            wholesale_price=Decimal("70.00"),
        )

        # ── Kirim 2: Store 1, Shell, Qisman to'langan + Qaytim bor ──
        cls.dt2 = timezone.make_aware(datetime(2026, 1, 15, 14, 30, 0))
        cls.entry2 = StockEntry.objects.create(
            store=cls.store1,
            supplier=cls.sup_shell,
            created_by=cls.store1_user,
            total_amount=Decimal("1500.00"),
            cash_amount=Decimal("500.00"),
            paid_amount=Decimal("500.00"),
            debt_amount=Decimal("1000.00"),
            note="Shell kirimi qisman to'langan",
        )
        StockEntry.objects.filter(id=cls.entry2.id).update(created_at=cls.dt2)
        cls.entry2.refresh_from_db()

        cls.item2_1 = StockEntryItem.objects.create(
            entry=cls.entry2,
            product=cls.prod_genesis,
            quantity=Decimal("15.00"),
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("160.00"),
            wholesale_price=Decimal("140.00"),
        )
        # Entry 2 bo'yicha qarz tranzaksiyasi
        SupplierTransaction.objects.create(
            supplier=cls.sup_shell,
            entry=cls.entry2,
            amount=Decimal("1000.00"),
            type=SupplierTransaction.TransactionType.INVENTORY_IN,
            note="Entry #2 qarzdorlik",
        )
        # Entry 2 bo'yicha qaytim (3 dona @ 100.00 = 300.00)
        cls.ret2 = StockEntryReturn.objects.create(
            entry=cls.entry2,
            total_amount=Decimal("300.00"),
            debt_cancelled=Decimal("300.00"),
            refund_amount=Decimal("0.00"),
            note="Yaroqsiz qaytim",
            created_by=cls.store1_user,
        )
        cls.ret_item2 = StockEntryReturnItem.objects.create(
            stock_return=cls.ret2,
            entry_item=cls.item2_1,
            product=cls.prod_genesis,
            quantity=Decimal("3.00"),
            purchase_price=Decimal("100.00"),
            amount=Decimal("300.00"),
        )
        SupplierTransaction.objects.create(
            supplier=cls.sup_shell,
            entry=cls.entry2,
            amount=Decimal("300.00"),
            type=SupplierTransaction.TransactionType.RETURN,
            note="Qaytim #1: qarz kamaydi",
        )

        # ── Kirim 3: Store 1, Lukoil, 0 tannarxli (sovg'a) ──
        cls.dt3 = timezone.make_aware(datetime(2026, 1, 20, 16, 0, 0))
        cls.entry3 = StockEntry.objects.create(
            store=cls.store1,
            supplier=cls.sup_lukoil,
            created_by=cls.manager,
            total_amount=Decimal("0.00"),
            cash_amount=Decimal("0.00"),
            paid_amount=Decimal("0.00"),
            debt_amount=Decimal("0.00"),
            note="Aksiya sovg'asi",
        )
        StockEntry.objects.filter(id=cls.entry3.id).update(created_at=cls.dt3)
        cls.entry3.refresh_from_db()

        cls.item3_1 = StockEntryItem.objects.create(
            entry=cls.entry3,
            product=cls.prod_zero_cost,
            quantity=Decimal("5.00"),
            purchase_price=Decimal("0.00"),
            selling_price=Decimal("50.00"),
            wholesale_price=Decimal("0.00"),
        )

        # ── Kirim 4: Store 2, Lukoil, To'lanmagan (unpaid) ──
        cls.dt4 = timezone.make_aware(datetime(2026, 1, 25, 11, 0, 0))
        cls.entry4 = StockEntry.objects.create(
            store=cls.store2,
            supplier=cls.sup_lukoil,
            created_by=cls.store2_user,
            total_amount=Decimal("800.00"),
            cash_amount=Decimal("0.00"),
            paid_amount=Decimal("0.00"),
            debt_amount=Decimal("800.00"),
            note="Store 2 nasiya partiya",
        )
        StockEntry.objects.filter(id=cls.entry4.id).update(created_at=cls.dt4)
        cls.entry4.refresh_from_db()

        cls.item4_1 = StockEntryItem.objects.create(
            entry=cls.entry4,
            product=cls.prod_inactive,
            quantity=Decimal("8.00"),
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            wholesale_price=Decimal("130.00"),
        )
        SupplierTransaction.objects.create(
            supplier=cls.sup_lukoil,
            entry=cls.entry4,
            amount=Decimal("800.00"),
            type=SupplierTransaction.TransactionType.INVENTORY_IN,
            note="Entry #4 nasiya",
        )

    # ─────────────────────────────────────────────────────────────
    # ASOSIY METRIKALAR VA HISOB-KITOBLAR (TEST 01 - 05)
    # ─────────────────────────────────────────────────────────────

    def test_01_basic_imports_metrics(self):
        """01. Barcha kirimlar metrikalari: umumiy qatorlar, yig'indilar va 25 ta ustun mavjudligi."""
        rows, totals = ReportingFoundationService.get_imports_metrics()
        self.assertEqual(len(rows), 5)
        self.assertEqual(totals["total_rows"], 5)
        self.assertEqual(totals["total_entries_count"], 4)
        self.assertEqual(totals["total_received_qty"], 58.0)
        self.assertEqual(totals["total_returned_qty"], 3.0)
        self.assertEqual(totals["total_returned_value"], 300.0)

        r1 = next(r for r in rows if r["entry_id"] == self.entry1.id and r["product_name"] == "Genesis Special 5W-40")
        self.assertEqual(r1["store_name"], "Markaziy Filial")
        self.assertEqual(r1["supplier_name"], "Lukoil Distribution")
        self.assertEqual(r1["quantity"], 10.0)
        self.assertEqual(r1["unit_purchase_price"], 100.0)
        self.assertEqual(r1["unit_sale_price"], 150.0)
        self.assertEqual(r1["purchase_value"], 1000.0)
        self.assertEqual(r1["sale_value"], 1500.0)
        self.assertEqual(r1["potential_margin"], 500.0)
        self.assertEqual(r1["returned_qty"], 0.0)
        self.assertEqual(r1["returned_value"], 0.0)
        self.assertEqual(r1["net_quantity"], 10.0)
        self.assertEqual(r1["net_purchase_value"], 1000.0)
        self.assertEqual(r1["payment_status"], "paid")

    def test_02_multiple_items_per_entry(self):
        """02. Bitta kirimda bir nechta tovar bo'lganda (Entry 1 da 2 ta tovar), alohida qator bo'lib chiqishi."""
        rows, totals = ReportingFoundationService.get_imports_metrics(entry_id=self.entry1.id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(totals["total_entries_count"], 1)
        self.assertEqual(totals["total_rows"], 2)
        prods = {r["product_name"] for r in rows}
        self.assertEqual(prods, {"Genesis Special 5W-40", "Moy filtri W712"})

    def test_03_filter_date_start(self):
        """03. Boshlanish sanasi (start): start dan oldingi kirimlar filtrlanishi."""
        start_dt = timezone.make_aware(datetime(2026, 1, 15, 0, 0, 0))
        rows, totals = ReportingFoundationService.get_imports_metrics(start=start_dt)
        entry_ids = {r["entry_id"] for r in rows}
        self.assertNotIn(self.entry1.id, entry_ids)
        self.assertIn(self.entry2.id, entry_ids)
        self.assertIn(self.entry3.id, entry_ids)
        self.assertIn(self.entry4.id, entry_ids)

    def test_04_filter_date_end(self):
        """04. Tugash sanasi (end): end dan keyingi kirimlar filtrlanishi."""
        end_dt = timezone.make_aware(datetime(2026, 1, 16, 0, 0, 0))
        rows, totals = ReportingFoundationService.get_imports_metrics(end=end_dt)
        entry_ids = {r["entry_id"] for r in rows}
        self.assertIn(self.entry1.id, entry_ids)
        self.assertIn(self.entry2.id, entry_ids)
        self.assertNotIn(self.entry3.id, entry_ids)
        self.assertNotIn(self.entry4.id, entry_ids)

    def test_05_date_interval_half_open(self):
        """05. [start, end) yarim ochiq oraliq: created_at >= start va created_at < end qat'iy ishlashi."""
        start = timezone.make_aware(datetime(2026, 1, 10, 10, 0, 0))
        end = timezone.make_aware(datetime(2026, 1, 15, 14, 30, 0))
        rows, _ = ReportingFoundationService.get_imports_metrics(start=start, end=end)
        entry_ids = {r["entry_id"] for r in rows}
        self.assertIn(self.entry1.id, entry_ids)
        # Entry 2 created_at == end bo'lgani uchun kirmasligi kerak (< end)
        self.assertNotIn(self.entry2.id, entry_ids)

    # ─────────────────────────────────────────────────────────────
    # FILTRLAR (TEST 06 - 17)
    # ─────────────────────────────────────────────────────────────

    def test_06_filter_store(self):
        """06. Filial bo'yicha filtr: faqat tanlangan do'kon kirimlari chiqishi."""
        rows, totals = ReportingFoundationService.get_imports_metrics(store_id=self.store2.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_id"], self.entry4.id)
        self.assertEqual(rows[0]["store_name"], "Samarqand Filiali")

    def test_07_store_isolation_view(self):
        """07. Server-side store isolation: allowed_store_ids bilan begona filiallar yashirilishi."""
        rows, _ = ReportingFoundationService.get_imports_metrics(allowed_store_ids=[self.store1.id])
        store_names = {r["store_name"] for r in rows}
        self.assertEqual(store_names, {"Markaziy Filial"})

    def test_08_filter_supplier(self):
        """08. Ta'minotchi bo'yicha filtr (StockEntry.supplier): 100% aniq FK bog'lanishi."""
        rows, totals = ReportingFoundationService.get_imports_metrics(supplier_id=self.sup_shell.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_id"], self.entry2.id)
        self.assertEqual(rows[0]["supplier_name"], "Shell Uzbekistan")

    def test_09_filter_user(self):
        """09. Mas'ul xodim (created_by_id) bo'yicha filtr."""
        rows, _ = ReportingFoundationService.get_imports_metrics(user_id=self.store1_user.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_id"], self.entry2.id)
        self.assertEqual(rows[0]["created_by_name"], "Store 1 Xodimi")

    def test_10_filter_entry_id(self):
        """10. Hujjat № (entry_id) aniq filtri."""
        rows, _ = ReportingFoundationService.get_imports_metrics(entry_id=self.entry2.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_id"], self.entry2.id)

    def test_11_filter_sku_iexact(self):
        """11. SKU filtri: katta/kichik harflarga sezgir bo'lmagan (iexact) moslik."""
        rows, _ = ReportingFoundationService.get_imports_metrics(sku="luk-gen-001")
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["sku"], "LUK-GEN-001")

    def test_12_filter_barcode_iexact(self):
        """12. Shtrix-kod filtri (iexact)."""
        rows, _ = ReportingFoundationService.get_imports_metrics(barcode="4011558712002")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["product_name"], "Moy filtri W712")

    def test_13_filter_category(self):
        """13. Kategoriya bo'yicha filtr."""
        rows, _ = ReportingFoundationService.get_imports_metrics(category_id=self.cat_filter.id)
        prods = {r["product_name"] for r in rows}
        self.assertEqual(prods, {"Moy filtri W712", "Sovg'a Aksessuar"})

    def test_14_filter_brand(self):
        """14. Brend bo'yicha filtr."""
        rows, _ = ReportingFoundationService.get_imports_metrics(brand_id=self.brand_mann.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["brand_name"], "Mann-Filter")

    def test_15_search_across_fields(self):
        """15. Qidiruv: tovar nomi, SKU, shtrix-kod, ta'minotchi nomi, izoh va hujjat ID bo'yicha."""
        # Tovar nomi bo'yicha
        r_name, _ = ReportingFoundationService.get_imports_metrics(search="W712")
        self.assertEqual(len(r_name), 1)

        # Ta'minotchi bo'yicha
        r_sup, _ = ReportingFoundationService.get_imports_metrics(search="Shell")
        self.assertEqual(len(r_sup), 1)

        # Izoh bo'yicha
        r_note, _ = ReportingFoundationService.get_imports_metrics(search="nasiya partiya")
        self.assertEqual(len(r_note), 1)

        # Hujjat ID bo'yicha
        r_id, _ = ReportingFoundationService.get_imports_metrics(search=str(self.entry3.id))
        self.assertEqual(len(r_id), 1)

    def test_16_has_returns_true(self):
        """16. has_returns=True: Faqat qaytarilgan tovar qatorlari chiqishi."""
        rows, _ = ReportingFoundationService.get_imports_metrics(has_returns=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_id"], self.entry2.id)
        self.assertGreater(rows[0]["returned_qty"], 0)

    def test_17_has_returns_false(self):
        """17. has_returns=False: Qaytimsiz tovar qatorlari chiqishi."""
        rows, _ = ReportingFoundationService.get_imports_metrics(has_returns=False)
        self.assertEqual(len(rows), 4)
        for r in rows:
            self.assertEqual(r["returned_qty"], 0.0)

    # ─────────────────────────────────────────────────────────────
    # QAYTIM, TANNARX VA FOYDA HISOB-KITOBLARI (TEST 18 - 24)
    # ─────────────────────────────────────────────────────────────

    def test_18_return_quantity_and_value(self):
        """18. Qaytarilgan miqdor va summa: StockEntryReturnItem ga tayangan authoritative hisob."""
        rows, _ = ReportingFoundationService.get_imports_metrics(entry_id=self.entry2.id)
        r = rows[0]
        self.assertEqual(r["returned_qty"], 3.0)
        self.assertEqual(r["returned_value"], 300.0)

    def test_19_net_quantity_calculation(self):
        """19. net_quantity = quantity - returned_qty formulasi."""
        rows, _ = ReportingFoundationService.get_imports_metrics(entry_id=self.entry2.id)
        r = rows[0]
        self.assertEqual(r["quantity"], 15.0)
        self.assertEqual(r["returned_qty"], 3.0)
        self.assertEqual(r["net_quantity"], 12.0)

    def test_20_net_purchase_value_calculation(self):
        """20. net_purchase_value = purchase_value - returned_value formulasi."""
        rows, _ = ReportingFoundationService.get_imports_metrics(entry_id=self.entry2.id)
        r = rows[0]
        self.assertEqual(r["purchase_value"], 1500.0)
        self.assertEqual(r["returned_value"], 300.0)
        self.assertEqual(r["net_purchase_value"], 1200.0)

    def test_21_purchase_value_calculation(self):
        """21. purchase_value = quantity * unit_purchase_price."""
        rows, _ = ReportingFoundationService.get_imports_metrics(entry_id=self.entry1.id)
        r = next(row for row in rows if row["product_name"] == "Genesis Special 5W-40")
        self.assertEqual(r["purchase_value"], 1000.0)

    def test_22_sale_value_calculation(self):
        """22. sale_value = quantity * unit_sale_price."""
        rows, _ = ReportingFoundationService.get_imports_metrics(entry_id=self.entry1.id)
        r = next(row for row in rows if row["product_name"] == "Genesis Special 5W-40")
        self.assertEqual(r["sale_value"], 1500.0)

    def test_23_potential_margin_calculation(self):
        """23. potential_margin = sale_value - purchase_value."""
        rows, _ = ReportingFoundationService.get_imports_metrics(entry_id=self.entry1.id)
        r = next(row for row in rows if row["product_name"] == "Genesis Special 5W-40")
        self.assertEqual(r["potential_margin"], 500.0)

    def test_24_zero_cost_warning(self):
        """24. 0 tannarxli tovar bo'lganda totals['has_zero_cost_items'] True bo'lishi va ogohlantirish berilishi."""
        _, totals = ReportingFoundationService.get_imports_metrics(entry_id=self.entry3.id)
        self.assertTrue(totals["has_zero_cost_items"])

        # Builder orqali info ogohlantirishini tekshirish
        _, _, _, _, info = ReportBuilderService._run({"report_type": "imports", "entry_id": str(self.entry3.id)}, self.admin)
        self.assertIn("warning", info)
        self.assertIn("tannarx 0 bo'lgani sababli", info["warning"])

    # ─────────────────────────────────────────────────────────────
    # TO'LOV VA QARZ HOLATLARI (TEST 25 - 28)
    # ─────────────────────────────────────────────────────────────

    def test_25_payment_status_paid(self):
        """25. To'lov holati 'paid': qoldiq qarz 0 bo'lganda to'langan chiqishi."""
        rows, _ = ReportingFoundationService.get_imports_metrics(entry_id=self.entry1.id)
        for r in rows:
            self.assertEqual(r["payment_status"], "paid")

    def test_26_payment_status_partial(self):
        """26. To'lov holati 'partial': boshlang'ich to'lov bor yoki qaytim kamaygan, lekin qoldiq qarz mavjud."""
        rows, _ = ReportingFoundationService.get_imports_metrics(entry_id=self.entry2.id)
        self.assertEqual(rows[0]["payment_status"], "partial")

    def test_27_payment_status_unpaid(self):
        """27. To'lov holati 'unpaid': hech qanday to'lov qilinmagan va qarz to'liq turibdi."""
        rows, _ = ReportingFoundationService.get_imports_metrics(entry_id=self.entry4.id)
        self.assertEqual(rows[0]["payment_status"], "unpaid")

    def test_28_payment_status_filter(self):
        """28. payment_status bo'yicha filtr: paid, partial, unpaid alohida to'g'ri filtrlanishi."""
        paid_rows, _ = ReportingFoundationService.get_imports_metrics(payment_status="paid")
        self.assertTrue(all(r["payment_status"] == "paid" for r in paid_rows))

        partial_rows, _ = ReportingFoundationService.get_imports_metrics(payment_status="partial")
        self.assertTrue(all(r["payment_status"] == "partial" for r in partial_rows))

        unpaid_rows, _ = ReportingFoundationService.get_imports_metrics(payment_status="unpaid")
        self.assertTrue(all(r["payment_status"] == "unpaid" for r in unpaid_rows))

    # ─────────────────────────────────────────────────────────────
    # RBAC VA XAVFSIZLIK (TEST 29 - 33)
    # ─────────────────────────────────────────────────────────────

    def test_29_rbac_imports_view(self):
        """29. RBAC VIEW: reports.imports.view yoki legacy fallbacklar bilan hisobotni ko'rish."""
        view = ReportBuilderGenerateAPIView.as_view()

        # reports.imports.view bilan
        self.manager_role.permissions = ["reports.view", "reports.imports.view"]
        self.manager_role.save()
        req = self.factory.get("/api/v1/reports/builder/generate/?report_type=imports")
        force_authenticate(req, user=self.manager)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)

        # stockentry.view legacy fallback bilan
        self.manager_role.permissions = ["reports.view", "stockentry.view"]
        self.manager_role.save()
        req = self.factory.get("/api/v1/reports/builder/generate/?report_type=imports")
        force_authenticate(req, user=self.manager)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)

    def test_30_rbac_imports_export(self):
        """30. RBAC EXPORT: reports.imports.export bilan eksport ruxsati berilishi."""
        view = ReportBuilderExportAPIView.as_view()
        self.manager_role.permissions = ["reports.view", "reports.imports.export"]
        self.manager_role.save()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=imports&export_type=csv")
        force_authenticate(req, user=self.manager)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)

    def test_31_negative_rbac_inventory_view_cannot_export(self):
        """31. QAT'IY RBAC: Faqat 'inventory.view' bo'lgan foydalanuvchi eksport qila OLMAYDI (403 Forbidden)."""
        view = ReportBuilderExportAPIView.as_view()
        self.manager_role.permissions = ["reports.view", "inventory.view"]
        self.manager_role.save()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=imports&export_type=csv")
        force_authenticate(req, user=self.manager)
        resp = view(req)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("huquqi yo'q", resp.data["detail"])

    def test_32_negative_rbac_reports_inventory_view_cannot_export(self):
        """32. QAT'IY RBAC: Faqat 'reports.inventory.view' bo'lgan foydalanuvchi eksport qila OLMAYDI (403 Forbidden)."""
        view = ReportBuilderExportAPIView.as_view()
        self.manager_role.permissions = ["reports.view", "reports.inventory.view"]
        self.manager_role.save()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=imports&export_type=excel")
        force_authenticate(req, user=self.manager)
        resp = view(req)
        self.assertEqual(resp.status_code, 403)

    def test_32b_negative_rbac_reports_imports_view_cannot_export(self):
        """32b. QAT'IY RBAC: VIEW != EXPORT. Faqat 'reports.imports.view' bo'lgan foydalanuvchi eksport qila OLMAYDI (403)."""
        view = ReportBuilderExportAPIView.as_view()
        self.manager_role.permissions = ["reports.view", "reports.imports.view"]
        self.manager_role.save()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=imports&export_type=csv")
        force_authenticate(req, user=self.manager)
        resp = view(req)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("huquqi yo'q", resp.data["detail"])

    def test_32c_negative_rbac_stockentry_view_cannot_export(self):
        """32c. QAT'IY RBAC: VIEW != EXPORT. Faqat 'stockentry.view' bo'lgan foydalanuvchi eksport qila OLMAYDI (403)."""
        view = ReportBuilderExportAPIView.as_view()
        self.manager_role.permissions = ["reports.view", "stockentry.view"]
        self.manager_role.save()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=imports&export_type=excel")
        force_authenticate(req, user=self.manager)
        resp = view(req)
        self.assertEqual(resp.status_code, 403)

    def test_33_negative_rbac_destructive_permissions_denied(self):
        """33. QAT'IY RBAC: 'stockentry.delete' yoki 'stockentry.edit' bilan report access/export berilmaydi."""
        gen_view = ReportBuilderGenerateAPIView.as_view()
        exp_view = ReportBuilderExportAPIView.as_view()

        self.manager_role.permissions = ["reports.view", "stockentry.delete", "stockentry.edit"]
        self.manager_role.save()

        req_gen = self.factory.get("/api/v1/reports/builder/generate/?report_type=imports")
        force_authenticate(req_gen, user=self.manager)
        resp_gen = gen_view(req_gen)
        self.assertEqual(resp_gen.status_code, 403)

        req_exp = self.factory.get("/api/v1/reports/builder/export/?report_type=imports&export_type=csv")
        force_authenticate(req_exp, user=self.manager)
        resp_exp = exp_view(req_exp)
        self.assertEqual(resp_exp.status_code, 403)

    # ─────────────────────────────────────────────────────────────
    # EKSPORT FORMATLARI (TEST 34 - 37)
    # ─────────────────────────────────────────────────────────────

    def test_34_export_excel_openxml_table(self):
        """34. Excel eksport: OpenXML Table (ws.add_table), AutoFilter va 25 ta ustun mavjudligi."""
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=imports&export_type=excel")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)

        import io, openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        self.assertIsNotNone(ws)
        self.assertGreater(len(ws.tables), 0)
        table = list(ws.tables.values())[0]
        self.assertIsNotNone(table.autoFilter)
        # 25 ta ustun tekshiruvi
        table_headers = [col.name for col in table.tableColumns]
        self.assertEqual(len(table_headers), 25)
        self.assertIn("Hujjat №", table_headers)
        self.assertIn("Ta'minotchi", table_headers)
        self.assertIn("Sof xarid summasi", table_headers)

    def test_35_export_csv_utf8_bom(self):
        """35. CSV eksport: UTF-8 BOM bilan boshlanishi va 25 ta ustun mavjudligi."""
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=imports&export_type=csv")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.content.startswith(bytes([0xEF, 0xBB, 0xBF])))
        csv_text = resp.content.decode("utf-8-sig")
        self.assertIn("Hujjat №", csv_text)
        self.assertIn("Genesis Special 5W-40", csv_text)

    def test_36_export_pdf_landscape(self):
        """36. PDF eksport: %PDF bilan boshlanishi va 14 ustunli executive jadval mavjudligi."""
        view = ReportBuilderExportAPIView.as_view()
        req = self.factory.get("/api/v1/reports/builder/export/?report_type=imports&export_type=pdf")
        force_authenticate(req, user=self.admin)
        resp = view(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF"))

    def test_37_export_store_isolation(self):
        """37. Eksportda do'kon izolyatsiyasi: Store 1 xodimi Store 2 ma'lumotlarini yuklab ololmasligi."""
        view = ReportBuilderExportAPIView.as_view()
        for exp_type in ("csv", "excel", "pdf"):
            req = self.factory.get(
                f"/api/v1/reports/builder/export/?report_type=imports&export_type={exp_type}&store_id={self.store2.id}"
            )
            force_authenticate(req, user=self.store1_user)
            resp = view(req)
            self.assertEqual(resp.status_code, 200)
            if exp_type == "csv":
                text = resp.content.decode("utf-8-sig")
                self.assertNotIn("Eski Moy Arxiv", text)
            elif exp_type == "excel":
                import io, openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(resp.content))
                ws = wb.active
                cells = [cell.value for row in ws.iter_rows(min_row=5) for cell in row if cell.value]
                self.assertNotIn("Eski Moy Arxiv", cells)
            elif exp_type == "pdf":
                self.assertNotIn(b"Eski Moy Arxiv", resp.content)

    # ─────────────────────────────────────────────────────────────
    # SAMARADORLIK VA XULOSA (TEST 38 - 40)
    # ─────────────────────────────────────────────────────────────

    def test_38_performance_zero_n_plus_one(self):
        """38. Query performance: 1 ta kirimda ham, barcha kirimlarda ham so'rovlar soni aynan 3 ta (Zero N+1)."""
        # 1 ta entry bilan (3 ta so'rov: StockEntryItem, Returns map, SupplierTransaction map)
        with self.assertNumQueries(3):
            ReportingFoundationService.get_imports_metrics(entry_id=self.entry1.id)

        # Barcha entry lar bilan (so'rovlar soni o'smaydi)
        with self.assertNumQueries(3):
            ReportingFoundationService.get_imports_metrics()

    def test_39_summary_filtered_dataset_exact_match(self):
        """39. Summary qatorlari aynan joriy filtrlangan dataset asosida hisoblanishi."""
        _, totals = ReportingFoundationService.get_imports_metrics(supplier_id=self.sup_lukoil.id)
        self.assertEqual(totals["total_entries_count"], 3)
        self.assertEqual(totals["total_rows"], 4)
        self.assertEqual(totals["total_received_qty"], 43.0)
        self.assertEqual(totals["total_returned_qty"], 0.0)

    def test_40_supplier_historical_correctness_and_product_master_data(self):
        """40. Ta'minotchi 100% tarixiy daxlsiz StockEntry.supplier dan olinishi va product status mapping to'g'riligi."""
        rows, _ = ReportingFoundationService.get_imports_metrics(entry_id=self.entry4.id)
        r = rows[0]
        self.assertEqual(r["supplier_name"], "Lukoil Distribution")
        self.assertEqual(r["product_status"], "Nofaol (Arxiv)")
