from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.reports.services.dashboard_service import ChartService, DateRangeResolver
from apps.reports.views.dashboard_view import DashboardAPIView
from apps.sales.models import Sale
from apps.store.models import Store
from apps.users.models.user import User


class DashboardYearlyChartTests(TestCase):
    """
    Dashboard yillik charti uchun testlar:
    - CASE 1: 2025-11 va 2025-12 ma'lumotlari 2026 Noyabr/Dekabrga o'tmasligi (Noyabr=None, Dekabr=None).
    - CASE 2: 2026-09 va 2026-10 savdolari o'z qiymatlarini qaytarishi.
    - CASE 3: 2026-11 yoki 2026-12 kelajak sanali savdolar bo'lsa ham chartda ko'rinmasligi (Noyabr=None, Dekabr=None).
    - CASE 4: 2026-01...2026-09 savdo bo'lmasa 0 bo'lishi, Noyabr/Dekabr None bo'lishi.
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

    def _create_sale(self, amount: Decimal, dt: datetime) -> Sale:
        sale = Sale.objects.create(
            store=self.store,
            seller=self.seller,
            total_amount=amount,
            paid_amount=amount,
            status=Sale.Status.PAID,
            payment_type=Sale.PaymentType.CASH,
        )
        Sale.objects.filter(id=sale.id).update(created_at=dt)
        return sale

    def test_case_1_and_case_2_past_year_isolation_and_current_months(self):
        """
        Bugun: 2026-10-01.
        CASE 1: 2025-11-15 (15000) va 2025-12-15 (25000) savdolari mavjud.
        CASE 2: 2026-09-10 (50000) va 2026-10-01 (70000) savdolari mavjud.
        2026-01-10 (30000) savdosi ham mavjud.

        Kutilgan:
        - 2025-11 va 2025-12 savdolari 2026 Noyabr/Dekabrga o'tmaydi.
        - Yanvar (m=1): 30000
        - Sentabr (m=9): 50000
        - Oktabr (m=10): 70000
        - Fevral-Avgust: 0
        - Noyabr (m=11): None
        - Dekabr (m=12): None
        """
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

            # Yanvar (indeks 0, m=1)
            self.assertEqual(data[0], Decimal("30000.00"))
            # Fevral..Avgust (indekslar 1..7) = 0
            for idx in range(1, 8):
                self.assertEqual(data[idx], Decimal("0"), f"Oy {idx + 1} nol bo'lishi kerak")

            # Sentabr (indeks 8, m=9) = 50000
            self.assertEqual(data[8], Decimal("50000.00"))
            # Oktabr (indeks 9, m=10) = 70000
            self.assertEqual(data[9], Decimal("70000.00"))

            # Noyabr (indeks 10, m=11) = None
            self.assertIsNone(data[10], "2025-11 savdosi 2026 Noyabrga o'tmasligi va None bo'lishi kerak")
            # Dekabr (indeks 11, m=12) = None
            self.assertIsNone(data[11], "2025-12 savdosi 2026 Dekabrga o'tmasligi va None bo'lishi kerak")

    def test_case_3_future_sales_not_included(self):
        """
        Bugun: 2026-10-01.
        CASE 3: DBda kelajak sana bilan Sale (2026-11-20 va 2026-12-05) yozilgan bo'lsa ham,
        joriy 2026-10-01 holatidagi yearly chart uni ko'rsatmasligi va Noyabr/Dekabr None bo'lishi shart.
        """
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        # Kelajakdagi noqonuniy/xato savdolar
        self._create_sale(Decimal("99999.00"), timezone.make_aware(datetime(2026, 11, 20, 10, 0, 0), tz))
        self._create_sale(Decimal("88888.00"), timezone.make_aware(datetime(2026, 12, 5, 10, 0, 0), tz))

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            data = res["data"]

            # Noyabr va Dekabr None bo'lishi shart
            self.assertIsNone(data[10], "Kelajakdagi 2026-11 savdosi chartda chiqmasligi kerak")
            self.assertIsNone(data[11], "Kelajakdagi 2026-12 savdosi chartda chiqmasligi kerak")

    def test_case_4_zero_sales_for_passed_months(self):
        """
        Bugun: 2026-10-01.
        CASE 4: 2026-01...2026-09 da umuman Sale bo'lmasa:
        Yanvar...Sentabr = 0
        Oktabr (joriy oy) = 0
        Noyabr / Dekabr = None
        """
        tz = timezone.get_current_timezone()
        mock_now = timezone.make_aware(datetime(2026, 10, 1, 12, 0, 0), tz)

        with patch("django.utils.timezone.now", return_value=mock_now):
            dr = DateRangeResolver.resolve("yearly")
            res = ChartService.get(store_id="all", dr=dr, period="yearly")
            data = res["data"]

            # Yanvar..Oktabr (indeks 0..9) = 0
            for idx in range(10):
                self.assertEqual(data[idx], Decimal("0"), f"Oy {idx + 1} 0 bo'lishi kerak")

            # Noyabr (indeks 10) = None
            self.assertIsNone(data[10])
            # Dekabr (indeks 11) = None
            self.assertIsNone(data[11])

    def test_api_view_yearly_integration(self):
        """
        GET /api/reports/dashboard/?period=yearly integratsion testi.
        API response ichidagi chart obyekti tekshiriladi.
        """
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
