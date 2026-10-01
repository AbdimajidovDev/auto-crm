from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.debts.models import CustomerDebt
from apps.products.models import Product
from apps.reports.services.dashboard_service import (
    ChartService,
    DateRangeResolver,
    KPIService,
    TopPartsService,
)
from apps.reports.views.dashboard_view import DashboardAPIView
from apps.sales.models import Payment, Sale, SaleItem, SaleReturn
from apps.sales.services.sale_return_service import SaleReturnService
from apps.store.models import Store, StoreUser
from apps.users.models.customers import Customer
from apps.users.models.user import User


class DashboardYearlyChartTests(TestCase):
    """
    Dashboard yillik charti va qaytarimlar (SaleReturn) hisob-kitobi uchun testlar:
    - CASE 1: 2025-11 va 2025-12 ma'lumotlari 2026 Noyabr/Dekabrga o'tmasligi (Noyabr=None, Dekabr=None).
    - CASE 2: 2026-09 va 2026-10 savdolari o'z qiymatlarini qaytarishi.
    - CASE 3: 2026-11 yoki 2026-12 kelajak sanali savdolar bo'lsa ham chartda ko'rinmasligi (Noyabr=None, Dekabr=None).
    - CASE 4: 2026-01...2026-09 savdo bo'lmasa 0 bo'lishi, Noyabr/Dekabr None bo'lishi.
    - CASE A: Full return (999,999,999 so'm) -> revenue=0, orders=0, chart=0.
    - CASE B: Partial return (Sale=1,000,000, Return=300,000) -> revenue=700,000, orders=1, chart=700,000.
    - CASE C: No return (1,000,000) -> revenue=1,000,000, orders=1, chart=1,000,000.
    - CASE D: Multiple sales (A=1,000,000 full return, B=500,000 no return) -> revenue=500,000, orders=1.
    - CASE E: Cross-month return (2026-09-30 Sale=1,000,000, 2026-10-01 Return=1,000,000) -> net=0.
    - TopPartsService da returned_quantity hisobga olinishi.
    - net_paid va net_debt matematik hisob-kitoblari.
    - DashboardAPIView orqali to'liq integratsion test.
    """

    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(name="Markaziy do'kon", phone_number="+998901112233")
        cls.seller = User.objects.create(
            phone_number="+998901234567",
            email="seller@example.com",
            is_superuser=True,
            is_staff=True,
        )
        StoreUser.objects.create(user=cls.seller, store=cls.store, is_active=True)
        cls.product = Product.objects.create(name="Moy filtri", min_stock=5)
        cls.product2 = Product.objects.create(name="Havo filtri", min_stock=5)

    def _create_sale(self, amount: Decimal, dt: datetime, paid_amount: Decimal | None = None) -> Sale:
        if paid_amount is None:
            paid_amount = amount
        sale = Sale.objects.create(
            store=self.store,
            seller=self.seller,
            total_amount=amount,
            paid_amount=paid_amount,
            status=Sale.Status.PAID if paid_amount == amount else Sale.Status.PARTIAL,
            payment_type=Sale.PaymentType.CASH,
        )
        Sale.objects.filter(id=sale.id).update(created_at=dt)
        return sale

    # ─────────────────────────────────────────────────────────────
    #  ESKI TEST CASES (1-4)
    # ─────────────────────────────────────────────────────────────

    def test_case_1_and_case_2_past_year_isolation_and_current_months(self):
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        # 2025 yildagi savdolar (o'tgan yil)
        self._create_sale(Decimal("15000.00"), timezone.make_aware(datetime(2025, 11, 15, 10, 0, 0), tz))
        self._create_sale(Decimal("25000.00"), timezone.make_aware(datetime(2025, 12, 15, 11, 0, 0), tz))

        # 2026 yildagi savdolar
        self._create_sale(Decimal("30000.00"), timezone.make_aware(datetime(2026, 1, 10, 10, 0, 0), tz))
        self._create_sale(Decimal("50000.00"), timezone.make_aware(datetime(2026, 9, 10, 14, 0, 0), tz))
        self._create_sale(Decimal("70000.00"), timezone.make_aware(datetime(2026, 10, 1, 9, 30, 0), tz))

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            self.assertEqual(dr.current_from, timezone.make_aware(datetime(2026, 1, 1, 0, 0, 0), tz))
            self.assertEqual(dr.current_to, mock_now)

            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            labels = res["labels"]
            data = res["data"]

            self.assertEqual(len(labels), 12)
            self.assertEqual(len(data), 12)

            self.assertEqual(data[0], Decimal("30000.00"))
            for idx in range(1, 8):
                self.assertEqual(data[idx], Decimal("0"), f"Oy {idx + 1} nol bo'lishi kerak")

            self.assertEqual(data[8], Decimal("50000.00"))
            self.assertEqual(data[9], Decimal("70000.00"))
            self.assertIsNone(data[10])
            self.assertIsNone(data[11])

    def test_case_3_future_sales_not_included(self):
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        self._create_sale(Decimal("99999.00"), timezone.make_aware(datetime(2026, 11, 20, 10, 0, 0), tz))
        self._create_sale(Decimal("88888.00"), timezone.make_aware(datetime(2026, 12, 5, 10, 0, 0), tz))

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            data = res["data"]

            self.assertIsNone(data[10])
            self.assertIsNone(data[11])

    def test_case_4_zero_sales_for_passed_months(self):
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            data = res["data"]

            for idx in range(10):
                self.assertEqual(data[idx], Decimal("0"), f"Oy {idx + 1} 0 bo'lishi kerak")

            self.assertIsNone(data[10])
            self.assertIsNone(data[11])

    # ─────────────────────────────────────────────────────────────
    #  YANGI BUSINESS LOGIC TEST CASES (A, B, C, D, E va boshqalar)
    # ─────────────────────────────────────────────────────────────

    def test_case_a_full_return_999m(self):
        """
        CASE A: 999,999,999 so'mlik Sale yaratildi va to'liq qaytarildi.
        Kutilgan: revenue = 0, orders = 0, chart = 0.
        """
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        amount = Decimal("999999999.00")
        sale = Sale.objects.create(
            store=self.store,
            seller=self.seller,
            total_amount=amount,
            paid_amount=amount,
            status=Sale.Status.PAID,
            payment_type=Sale.PaymentType.CASH,
        )
        Sale.objects.filter(id=sale.id).update(created_at=mock_now)
        item = SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=Decimal("10"),
            unit_price=Decimal("99999999.90"),
            purchase_price=Decimal("50000000.00"),
            total_price=amount,
        )
        Payment.objects.create(sale=sale, amount=amount, type=Payment.Type.CASH)

        # To'liq qaytarish
        SaleReturnService.create_return(
            user=self.seller,
            data={
                "sale": sale.id,
                "comment": "Full return of 999M",
                "items": [{"sale_item": item.id, "quantity": Decimal("10")}],
                "payments": [{"type": "cash", "amount": amount}],
            },
        )
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.RETURNED)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            kpi = KPIService.get(store_id="all", dr=dr)
            chart = ChartService.get(store_id="all", dr=dr, period="yearly")

            # KPI tekshiruvi
            self.assertEqual(kpi["revenue"], Decimal("0.00"))
            self.assertEqual(kpi["orders"], 0)
            self.assertEqual(kpi["debt"], Decimal("0.00"))

            # Chart tekshiruvi: 10-oy (Oktabr, indeks 9) 0 bo'lishi kerak
            self.assertEqual(chart["data"][9], Decimal("0.00"))

    def test_case_b_partial_return(self):
        """
        CASE B: Sale = 1,000,000 (10 dona x 100,000).
        Qaytarildi = 300,000 (3 dona x 100,000).
        Kutilgan: revenue = 700,000, orders = 1, chart = 700,000.
        """
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        amount = Decimal("1000000.00")
        sale = Sale.objects.create(
            store=self.store,
            seller=self.seller,
            total_amount=amount,
            paid_amount=amount,
            status=Sale.Status.PAID,
            payment_type=Sale.PaymentType.CASH,
        )
        Sale.objects.filter(id=sale.id).update(created_at=mock_now)
        item = SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=Decimal("10"),
            unit_price=Decimal("100000.00"),
            purchase_price=Decimal("50000.00"),
            total_price=amount,
        )
        Payment.objects.create(sale=sale, amount=amount, type=Payment.Type.CASH)

        # 3 dona qisman qaytarish (300,000)
        SaleReturnService.create_return(
            user=self.seller,
            data={
                "sale": sale.id,
                "comment": "Partial return 3 pcs",
                "items": [{"sale_item": item.id, "quantity": Decimal("3")}],
                "payments": [{"type": "cash", "amount": Decimal("300000.00")}],
            },
        )
        sale.refresh_from_db()
        self.assertNotEqual(sale.status, Sale.Status.RETURNED)  # Status RETURNED bo'lmaydi

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            kpi = KPIService.get(store_id="all", dr=dr)
            chart = ChartService.get(store_id="all", dr=dr, period="yearly")

            self.assertEqual(kpi["revenue"], Decimal("700000.00"))
            self.assertEqual(kpi["orders"], 1)
            self.assertEqual(kpi["paid"], Decimal("700000.00"))
            self.assertEqual(kpi["debt"], Decimal("0.00"))

            self.assertEqual(chart["data"][9], Decimal("700000.00"))

    def test_case_c_no_return(self):
        """
        CASE C: Sale = 1,000,000, qaytarim yo'q.
        Kutilgan: revenue = 1,000,000, orders = 1, chart = 1,000,000.
        """
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        amount = Decimal("1000000.00")
        sale = self._create_sale(amount, mock_now)
        SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=Decimal("10"),
            unit_price=Decimal("100000.00"),
            purchase_price=Decimal("50000.00"),
            total_price=amount,
        )

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            kpi = KPIService.get(store_id="all", dr=dr)
            chart = ChartService.get(store_id="all", dr=dr, period="yearly")

            self.assertEqual(kpi["revenue"], Decimal("1000000.00"))
            self.assertEqual(kpi["orders"], 1)
            self.assertEqual(chart["data"][9], Decimal("1000000.00"))

    def test_case_d_multiple_sales_with_full_and_no_return(self):
        """
        CASE D:
        Sale A = 1,000,000 (to'liq qaytarilgan)
        Sale B = 500,000 (qaytarim yo'q)
        Kutilgan: revenue = 500,000, orders = 1, chart = 500,000.
        """
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        # Sale A: 1,000,000
        sale_a = self._create_sale(Decimal("1000000.00"), mock_now)
        item_a = SaleItem.objects.create(
            sale=sale_a,
            product=self.product,
            quantity=Decimal("10"),
            unit_price=Decimal("100000.00"),
            purchase_price=Decimal("50000.00"),
            total_price=Decimal("1000000.00"),
        )
        Payment.objects.create(sale=sale_a, amount=Decimal("1000000.00"), type=Payment.Type.CASH)
        SaleReturnService.create_return(
            user=self.seller,
            data={
                "sale": sale_a.id,
                "comment": "Full return A",
                "items": [{"sale_item": item_a.id, "quantity": Decimal("10")}],
                "payments": [{"type": "cash", "amount": Decimal("1000000.00")}],
            },
        )

        # Sale B: 500,000
        sale_b = self._create_sale(Decimal("500000.00"), mock_now)
        SaleItem.objects.create(
            sale=sale_b,
            product=self.product2,
            quantity=Decimal("5"),
            unit_price=Decimal("100000.00"),
            purchase_price=Decimal("50000.00"),
            total_price=Decimal("500000.00"),
        )

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            kpi = KPIService.get(store_id="all", dr=dr)
            chart = ChartService.get(store_id="all", dr=dr, period="yearly")

            self.assertEqual(kpi["revenue"], Decimal("500000.00"))
            self.assertEqual(kpi["orders"], 1)
            self.assertEqual(chart["data"][9], Decimal("500000.00"))

    def test_case_e_cross_month_return(self):
        """
        CASE E:
        2026-09-30 da Sale = 1,000,000 yaratildi.
        2026-10-01 da to'liq qaytarildi.
        Check-level netting semantikasi bo'yicha:
        Ushbu chekning sof summasi (net_total) = 0.
        Sentabr (m=9) = 0, Oktabr (m=10) = 0, YTD revenue = 0.
        """
        tz = timezone.get_current_timezone()
        dt_sale = timezone.make_aware(datetime(2026, 9, 30, 16, 0, 0), tz)
        dt_return = timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz)
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        sale = self._create_sale(Decimal("1000000.00"), dt_sale)
        item = SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=Decimal("10"),
            unit_price=Decimal("100000.00"),
            purchase_price=Decimal("50000.00"),
            total_price=Decimal("1000000.00"),
        )
        Payment.objects.create(sale=sale, amount=Decimal("1000000.00"), type=Payment.Type.CASH)

        with patch("django.utils.timezone.now", return_value=dt_return):
            SaleReturnService.create_return(
                user=self.seller,
                data={
                    "sale": sale.id,
                    "comment": "Return next day",
                    "items": [{"sale_item": item.id, "quantity": Decimal("10")}],
                    "payments": [{"type": "cash", "amount": Decimal("1000000.00")}],
                },
            )

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            kpi = KPIService.get(store_id="all", dr=dr)
            chart = ChartService.get(store_id="all", dr=dr, period="yearly")

            self.assertEqual(kpi["revenue"], Decimal("0.00"))
            self.assertEqual(kpi["orders"], 0)
            self.assertEqual(chart["data"][8], Decimal("0.00"))  # Sentabr = 0
            self.assertEqual(chart["data"][9], Decimal("0.00"))  # Oktabr = 0

    def test_top_parts_service_with_returns(self):
        """
        TopPartsService da qaytarilgan donalar (returned_quantity) chegirilishi:
        - Product A: 10 dona sotildi, 4 dona qaytarildi -> sold = 6 dona, rev = 600,000.
        - Product B: 5 dona sotildi, 5 dona qaytarildi (to'liq) -> ro'yxatdan chiqib ketishi kerak.
        """
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        # Sale 1: Product A (10 dona x 100,000)
        sale1 = self._create_sale(Decimal("1000000.00"), mock_now)
        item1 = SaleItem.objects.create(
            sale=sale1,
            product=self.product,
            quantity=Decimal("10"),
            unit_price=Decimal("100000.00"),
            purchase_price=Decimal("50000.00"),
            total_price=Decimal("1000000.00"),
        )
        Payment.objects.create(sale=sale1, amount=Decimal("1000000.00"), type=Payment.Type.CASH)
        SaleReturnService.create_return(
            user=self.seller,
            data={
                "sale": sale1.id,
                "comment": "Return 4 pcs of Product A",
                "items": [{"sale_item": item1.id, "quantity": Decimal("4")}],
                "payments": [{"type": "cash", "amount": Decimal("400000.00")}],
            },
        )

        # Sale 2: Product B (5 dona x 50,000 = 250,000 to'liq qaytarildi)
        sale2 = self._create_sale(Decimal("250000.00"), mock_now)
        item2 = SaleItem.objects.create(
            sale=sale2,
            product=self.product2,
            quantity=Decimal("5"),
            unit_price=Decimal("50000.00"),
            purchase_price=Decimal("30000.00"),
            total_price=Decimal("250000.00"),
        )
        Payment.objects.create(sale=sale2, amount=Decimal("250000.00"), type=Payment.Type.CASH)
        SaleReturnService.create_return(
            user=self.seller,
            data={
                "sale": sale2.id,
                "comment": "Full return Product B",
                "items": [{"sale_item": item2.id, "quantity": Decimal("5")}],
                "payments": [{"type": "cash", "amount": Decimal("250000.00")}],
            },
        )

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            top_parts = TopPartsService.get(store_id="all", dr=dr)

            # Product B to'liq qaytarilgani sababli ro'yxatda faqat Product A qolishi kerak
            self.assertEqual(len(top_parts), 1)
            self.assertEqual(top_parts[0]["name"], self.product.name)
            self.assertEqual(top_parts[0]["sold"], Decimal("6.00"))
            self.assertEqual(top_parts[0]["rev"], Decimal("600000.00"))

    def test_net_paid_and_net_debt_mathematical_consistency(self):
        """
        net_paid va net_debt hisob-kitoblarining to'g'riligi:
        - 1,000,000 so'mlik sotuv (600,000 to'langan, 400,000 qarz).
        - 300,000 so'mlik qaytarim bo'lganda: qarz 100,000 ga tushadi, to'langan 600,000 saqlanadi.
        """
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        customer = Customer.objects.create(full_name="Qarzli mijoz", phone_number="+998909876543")

        sale = Sale.objects.create(
            store=self.store,
            seller=self.seller,
            customer=customer,
            total_amount=Decimal("1000000.00"),
            paid_amount=Decimal("600000.00"),
            status=Sale.Status.PARTIAL,
            payment_type=Sale.PaymentType.MIXED,
        )
        Sale.objects.filter(id=sale.id).update(created_at=mock_now)
        item = SaleItem.objects.create(
            sale=sale,
            product=self.product,
            quantity=Decimal("10"),
            unit_price=Decimal("100000.00"),
            purchase_price=Decimal("50000.00"),
            total_price=Decimal("1000000.00"),
        )
        Payment.objects.create(sale=sale, customer=customer, amount=Decimal("600000.00"), type=Payment.Type.CASH)
        CustomerDebt.objects.create(
            customer=customer,
            sale=sale,
            amount=Decimal("400000.00"),
            type=CustomerDebt.Type.INCREASE,
        )

        # 300,000 qaytarish (qarzdan yopiladi)
        SaleReturnService.create_return(
            user=self.seller,
            data={
                "sale": sale.id,
                "comment": "Reduce debt return",
                "items": [{"sale_item": item.id, "quantity": Decimal("3")}],
            },
        )

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            kpi = KPIService.get(store_id="all", dr=dr)

            # net_total = 700,000, net_paid = 600,000, net_debt = 100,000
            self.assertEqual(kpi["revenue"], Decimal("700000.00"))
            self.assertEqual(kpi["paid"], Decimal("600000.00"))
            self.assertEqual(kpi["debt"], Decimal("100000.00"))

    def test_api_view_yearly_integration(self):
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        # 2025 savdosi
        self._create_sale(Decimal("10000.00"), timezone.make_aware(datetime(2025, 11, 20, 10, 0, 0), tz))
        # 2026-09 va 2026-10 savdolari
        self._create_sale(Decimal("45000.00"), timezone.make_aware(datetime(2026, 9, 15, 10, 0, 0), tz))
        self._create_sale(Decimal("65000.00"), timezone.make_aware(datetime(2026, 10, 1, 10, 0, 0), tz))

        factory = APIRequestFactory()
        request = factory.get("/api/reports/dashboard/", {"period": "yearly", "store_id": "all"})
        force_authenticate(request, user=self.seller)

        with patch("django.utils.timezone.now", return_value=mock_now):
            response = DashboardAPIView.as_view()(request)
            self.assertEqual(response.status_code, 200)

            chart = response.data["chart"]
            self.assertIn("labels", chart)
            self.assertIn("data", chart)

            labels = chart["labels"]
            data = chart["data"]

            self.assertEqual(labels, [
                "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
                "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"
            ])
            self.assertEqual(data[8], Decimal("45000.00"))  # Sentabr
            self.assertEqual(data[9], Decimal("65000.00"))  # Oktabr
            self.assertIsNone(data[10])  # Noyabr
            self.assertIsNone(data[11])  # Dekabr

            # KPI
            kpi = response.data["kpi"]
            self.assertEqual(kpi["revenue"], Decimal("110000.00"))  # 45000 + 65000
            self.assertEqual(kpi["orders"], 2)
