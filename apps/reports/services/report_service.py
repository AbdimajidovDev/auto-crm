# from .summary_service import SummaryService
# from .branch_service import BranchService
# from .debt_service import DebtService
# from .filter_service import ReportFilterService


# ===========================   Reports   ==================================================

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import (
    Case,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.contract.models import SupplierTransaction
from apps.sales.models import Payment, Sale, SaleItem, SaleReturn
from apps.store.models import Store
from ...debts.models import CustomerDebt

# ─────────────────────────────────────────────
#  Konstantalar
# ─────────────────────────────────────────────
TOP_PRODUCTS_LIMIT = 5

PAYMENT_METHOD_LABELS = {
    "cash": "Naqd",
    "card": "Karta",
    "debt": "Qarz",
}


# ─────────────────────────────────────────────
#  Filtr yordamchilari
# ─────────────────────────────────────────────
class ReportFilterService:
    """
    store_id va sana oralig'ini validatsiya qiladi.
    store_id='all' yoki None → None (barcha do'konlar).
    """

    @staticmethod
    def resolve_store(store_id: str | None) -> int | None:
        if not store_id or store_id == "all":
            return None
        try:
            sid = int(store_id)
        except (ValueError, TypeError):
            raise ValidationError({"store_id": "Noto'g'ri qiymat."})
        if not Store.objects.filter(id=sid).exists():
            raise ValidationError({"store_id": "Do'kon topilmadi."})
        return sid

    @staticmethod
    def resolve_dates(
        filter_type: str | None,
        from_raw: str | None,
        to_raw: str | None,
    ) -> tuple[date, date]:
        """
        1. from/to berilsa — shu oraliqni ishlatadi.
        2. Aks holda filter_type bo'yicha avtomatik hisoblaydi.
        """
        if from_raw and to_raw:
            try:
                return (
                    date.fromisoformat(from_raw),
                    date.fromisoformat(to_raw),
                )
            except ValueError:
                raise ValidationError({"from/to": "ISO format bo'lishi kerak: YYYY-MM-DD."})

        today = timezone.localdate()
        if filter_type == "weekly":
            # Joriy haftaning Dushanbasi
            return today - timedelta(days=today.weekday()), today
        if filter_type == "yearly":
            return today - timedelta(days=365), today
        # default: monthly
        return today - timedelta(days=30), today


def _store_q(store_id: int | None, field: str = "store_id") -> Q:
    """Store filter Q — None bo'lsa bo'sh Q (barcha qatorlar)."""
    return Q(**{field: store_id}) if store_id else Q()


# ─────────────────────────────────────────────
#  Returns subquery — ikki joyda takrorlanmaslik uchun
# ─────────────────────────────────────────────
def _returns_subquery():
    """
    Har bir Sale uchun qaytarilgan summani hisoblaydi.
    SummaryService va BranchService ikkalasi ishlatadi.
    """
    return (
        SaleReturn.objects
        .filter(sale_id=OuterRef("id"))
        .values("sale")
        .annotate(total=Sum("total_refund"))
        .values("total")[:1]
    )


def _base_sales_qs(date_from: date, date_to: date, store_id: int | None):
    """
    Qaytarilgan sotuvlarni chiqarib tashlagan, annotate qilingan asosiy Sales QS.
    SummaryService, BranchService va boshqalar uchun umumiy base.
    """
    # ⚠️ MUAMMO [KRITIK/PERF]: `created_at__date__gte/lte` — `__date` transform ustunni SQL'da
    # `DATE(created_at)` / `CAST(created_at AS date)` ga o'raydi. Bu so'rovni NON-SARGABLE qiladi:
    # `created_at` ustunidagi indeks (hozir yo'q ham) bu holda ISHLAMAYDI, chunki index xom ustunga,
    # so'rov esa funksiya natijasiga qo'yilgan. Natija: bu base QS SummaryService, BranchService,
    # TopProducts va boshqa barcha report bloklarida ishlatilgani sabab, HAR report so'rovida ~65k
    # Sale qatori TO'LIQ skanerlanadi (bir dashboard = bir necha shunday skan).
    # ✅ YECHIM:
    #   1) `__date` o'rniga xom datetime chegara bilan filtrlash (sargable, indeks ishlaydi):
    #        from datetime import datetime, time
    #        start = datetime.combine(date_from, time.min)          # 00:00:00
    #        end   = datetime.combine(date_to,   time.max)          # 23:59:59.999999
    #        .filter(created_at__gte=start, created_at__lte=end)
    #      (yoki yarim-ochiq: created_at__lt = date_to + 1 kun)
    #   2) Sale modeliga `created_at` (va `store`,`created_at`) kompozit indeksini qo'shish
    #      — sales/models.py dagi izohga qarang.
    #   3) Vaqt mintaqasi muhim bo'lsa, xom chegaralarni `timezone.make_aware` bilan hosil qilish.
    return (
        Sale.objects
        .filter(
            created_at__date__gte=date_from,
            created_at__date__lte=date_to,
        )
        .filter(_store_q(store_id))
        .exclude(status=Sale.Status.RETURNED)
        .annotate(
            refunded=Coalesce(
                Subquery(_returns_subquery(), output_field=DecimalField()),
                Value(Decimal("0"), output_field=DecimalField()),
            )
        )
    )


# ─────────────────────────────────────────────
#  Summary
# ─────────────────────────────────────────────
class SummaryService:
    """
    Umumiy moliyaviy ko'rsatkichlar.
    2 ta SQL: sales aggregate + items profit aggregate.
    """

    @staticmethod
    def get(date_from: date, date_to: date, store_id: int | None) -> dict:
        sales_qs = _base_sales_qs(date_from, date_to, store_id)

        agg = sales_qs.aggregate(
            total_revenue=Coalesce(
                Sum(F("total_amount") - F("refunded")),
                Value(Decimal("0")), output_field=DecimalField(),
            ),
            total_orders=Count("id"),
            total_customers=Count(
                "customer", distinct=True,
                filter=Q(customer__isnull=False),
            ),
        )

        # Foyda: (unit_price - purchase_price) * quantity
        # purchase_price NULL bo'lsa Coalesce(0) bilan xavfsiz hisoblash
        items_qs = (
            SaleItem.objects
            .filter(
                sale__created_at__date__gte=date_from,
                sale__created_at__date__lte=date_to,
                sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIAL],
            )
            .filter(_store_q(store_id, "sale__store_id"))
        )
        total_profit = items_qs.aggregate(
            profit=Coalesce(
                Sum(
                    ExpressionWrapper(
                        (F("unit_price") - Coalesce(
                            F("purchase_price"),
                            Value(Decimal("0"), output_field=DecimalField()),
                        )) * F("quantity"),
                        output_field=DecimalField(),
                    )
                ),
                Value(Decimal("0")), output_field=DecimalField(),
            )
        )["profit"]

        revenue = agg["total_revenue"]
        orders  = agg["total_orders"] or 0

        return {
            "totalRevenue":      revenue,
            "totalProfit":       total_profit,
            "totalExpenses":     revenue - total_profit,
            "totalOrders":       orders,
            "averageOrderValue": round(revenue / orders, 2) if orders else Decimal("0"),
            "totalCustomers":    agg["total_customers"] or 0,
        }


# ─────────────────────────────────────────────
#  Branch Statistics
# ─────────────────────────────────────────────
class BranchService:
    """
    Har bir do'kon uchun daromat, buyurtmalar va mijozlar soni.
    1 ta SQL.
    """

    @staticmethod
    def get(date_from: date, date_to: date, store_id: int | None) -> list[dict]:
        return list(
            _base_sales_qs(date_from, date_to, store_id)
            .values("store_id", "store__name")
            .annotate(
                revenue=Coalesce(
                    Sum(F("total_amount") - F("refunded")),
                    Value(Decimal("0")), output_field=DecimalField(),
                ),
                orders=Count("id"),
                customers=Count(
                    "customer", distinct=True,
                    filter=Q(customer__isnull=False),
                ),
            )
            .order_by("-revenue")
        )


# ─────────────────────────────────────────────
#  Category Statistics
# ─────────────────────────────────────────────
class CategoryStatisticsService:
    """
    Kategoriya bo'yicha sotuv ulushi (%).
    SaleItem → product → category JOIN — 1 ta SQL.
    """

    @staticmethod
    def get(date_from: date, date_to: date, store_id: int | None) -> list[dict]:
        qs = (
            SaleItem.objects
            .filter(
                sale__created_at__date__gte=date_from,
                sale__created_at__date__lte=date_to,
                sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIAL],
            )
            .filter(_store_q(store_id, "sale__store_id"))
            .values("product__category__name")
            .annotate(
                revenue=Coalesce(
                    Sum("total_price"),
                    Value(Decimal("0")), output_field=DecimalField(),
                )
            )
            .order_by("-revenue")
        )

        rows     = list(qs)
        total    = sum(r["revenue"] for r in rows) or Decimal("1")  # 0 ga bo'lishdan saqlanish

        return [
            {
                "categoryName": r["product__category__name"] or "Noma'lum",
                "revenue":      r["revenue"],
                "percent":      round(float(r["revenue"] / total * 100), 1),
            }
            for r in rows
        ]


# ─────────────────────────────────────────────
#  Top Selling Products
# ─────────────────────────────────────────────
class TopProductsService:
    """
    Eng ko'p sotilgan TOP_PRODUCTS_LIMIT ta mahsulot.
    SaleItem → product → category JOIN — 1 ta SQL.
    """

    @staticmethod
    def get(date_from: date, date_to: date, store_id: int | None) -> list[dict]:
        rows = (
            SaleItem.objects
            .filter(
                sale__created_at__date__gte=date_from,
                sale__created_at__date__lte=date_to,
            )
            .filter(_store_q(store_id, "sale__store_id"))
            .values("product_id", "product__name", "product__category__name")
            .annotate(
                totalSold=Coalesce(Sum("quantity"), Value(0)),
                totalRevenue=Coalesce(
                    Sum("total_price"),
                    Value(Decimal("0")), output_field=DecimalField(),
                ),
            )
            .order_by("-totalSold")
            [:TOP_PRODUCTS_LIMIT]
        )

        return [
            {
                "rank":         i + 1,
                "productId":    r["product_id"],
                "name":         r["product__name"],
                "category":     r["product__category__name"] or "Noma'lum",
                "totalSold":    r["totalSold"],
                "totalRevenue": r["totalRevenue"],
            }
            for i, r in enumerate(rows)
        ]


# ─────────────────────────────────────────────
#  Payment Structure
# ─────────────────────────────────────────────
class PaymentStructureService:
    """
    To'lov turlari bo'yicha taqsimot: naqd, karta, qarz.
    Payment modeli orqali — 1 ta SQL.
    """

    @staticmethod
    def get(date_from: date, date_to: date, store_id: int | None) -> list[dict]:
        qs = (
            Payment.objects
            .filter(
                created_at__date__gte=date_from,
                created_at__date__lte=date_to,
            )
            .filter(_store_q(store_id, "sale__store_id"))
            .values("type")
            .annotate(
                count=Count("id"),
                amount=Coalesce(
                    Sum("amount"),
                    Value(Decimal("0")), output_field=DecimalField(),
                ),
            )
            .order_by("-amount")
        )

        rows        = list(qs)
        total_amount = sum(r["amount"] for r in rows) or Decimal("1")

        # Qarz (debt) — Payment modeli orqali emas, Sale.status='debt' orqali
        # Agar Payment modelida 'debt' type yo'q bo'lsa, Sale orqali qo'shamiz
        debt_agg = (
            Sale.objects
            .filter(
                created_at__date__gte=date_from,
                created_at__date__lte=date_to,
                status=Sale.Status.DEBT,
            )
            .filter(_store_q(store_id))
            .aggregate(
                count=Count("id"),
                amount=Coalesce(
                    Sum(F("total_amount") - F("paid_amount")),
                    Value(Decimal("0")), output_field=DecimalField(),
                ),
            )
        )

        result = []

        for r in rows:
            if r["type"] == "debt":
                continue  # Sale orqali alohida qo'shamiz
            result.append({
                "method":  PAYMENT_METHOD_LABELS.get(r["type"], r["type"]),
                "count":   r["count"],
                "amount":  r["amount"],
                "percent": f"{round(float(r['amount'] / total_amount * 100), 1)}%",
            })

        if debt_agg["amount"]:
            result.append({
                "method":  "Qarz",
                "count":   debt_agg["count"],
                "amount":  debt_agg["amount"],
                "percent": f"{round(float(debt_agg['amount'] / total_amount * 100), 1)}%",
            })

        return result


# ─────────────────────────────────────────────
#  Debts
# ─────────────────────────────────────────────
class DebtService:
    """
    Mijoz va supplier qarzlari.
    Har biri 1 ta SQL.
    """

    @staticmethod
    def customer_debts(store_id: int | None) -> list[dict]:
        qs = (
            CustomerDebt.objects
            .filter(_store_q(store_id, "sale__store_id"))
            .values("customer__full_name", "customer__phone_number")
            .annotate(
                inc=Coalesce(Sum("amount", filter=Q(type="i")), Value(Decimal("0")), output_field=DecimalField()),
                dec=Coalesce(Sum("amount", filter=Q(type="d")), Value(Decimal("0")), output_field=DecimalField()),
            )
        )
        return [
            {
                "customerName": r["customer__full_name"],
                "phone":        r["customer__phone_number"],
                "debt":         r["inc"] - r["dec"],
            }
            for r in qs
            if r["inc"] - r["dec"] > 0       # faqat musbat qarzlar
        ]

    @staticmethod
    def supplier_debts(store_id: int | None) -> list[dict]:
        qs = (
            SupplierTransaction.objects
            .filter(_store_q(store_id, "entry__store_id"))
            .values("supplier__name")
            .annotate(
                inc=Coalesce(Sum("amount", filter=Q(type="in")),  Value(Decimal("0")), output_field=DecimalField()),
                dec=Coalesce(Sum("amount", filter=Q(type="pay")), Value(Decimal("0")), output_field=DecimalField()),
            )
        )
        return [
            {
                "supplierName": r["supplier__name"],
                "debt":         r["inc"] - r["dec"],
            }
            for r in qs
            if r["inc"] - r["dec"] > 0
        ]


# ─────────────────────────────────────────────
#  Report Facade
# ─────────────────────────────────────────────
class ReportService:
    """
    Barcha service'larni birlashtiradi.
    Cache key deterministik — params tartibi muhim emas.
    """

    @staticmethod
    def get(params: dict) -> dict:
        store_id    = ReportFilterService.resolve_store(params.get("store_id"))
        filter_type = params.get("filter", "monthly")
        date_from, date_to = ReportFilterService.resolve_dates(
            filter_type,
            params.get("from"),
            params.get("to"),
        )

        # Deterministik cache key
        cache_key = (
            f"report:{store_id}:{filter_type}:{date_from}:{date_to}"
        )
        cached = cache.get(cache_key)
        if cached:
            return cached

        data = {
            "summary":            SummaryService.get(date_from, date_to, store_id),
            "branchStatistics":   BranchService.get(date_from, date_to, store_id),
            "categoryStatistics": CategoryStatisticsService.get(date_from, date_to, store_id),
            "topSellingProducts": TopProductsService.get(date_from, date_to, store_id),
            "paymentStructure":   PaymentStructureService.get(date_from, date_to, store_id),
            "debts": {
                "customerDebts":  DebtService.customer_debts(store_id),
                "supplierDebts":  DebtService.supplier_debts(store_id),
            },
        }

        cache.set(cache_key, data, timeout=60)
        return data



# class ReportService:
#
#     @staticmethod
#     def get(user, params):
#
#         store_ids = ReportFilterService.resolve_store(params.get("storeId"))
#
#         filter_type = params.get("filter", "monthly")
#
#         date_from, date_to = DateValidator.validate(
#             params.get("from"),
#             params.get("to")
#         )
#
#         # 🔥 SHU YERGA
#         if not date_from:
#             date_from, date_to = DateRangeResolver.resolve(filter_type)
#
#         summary = SummaryService.get(date_from, date_to, store_ids)
#
#         branches = BranchService.get(date_from, date_to, store_ids)
#
#         items_qs = SaleItem.objects.filter(
#             sale__created_at__range=(date_from, date_to)
#         )
#         # ⚠️ MUAMMO [PERFORMANCE]: `items_qs` `select_related("sale")` bilan yengillashtirilmagan.
#         # Sabab: keyingi chart/service hisoblar sale sanasi va store filteriga tayanadi.
#         # Natija: service kengaysa sale FK bo'yicha qo'shimcha querylar paydo bo'lishi mumkin.
#         # ✅ YECHIM:
#         # items_qs = SaleItem.objects.select_related("sale", "product").filter(
#         #     sale__created_at__range=(date_from, date_to)
#         # )
#         # N+1 / ma'lumot: keyingi `ChartService.profit_trend` va boshqa hisoblar `purchase_price`ga
#         # tayangan — NULL bo'lsa natijalar buziladi; `select_related("sale")` ixtiyoriy yengillashtirish.
#
#         if store_ids:
#             items_qs = items_qs.filter(sale__store_id__in=store_ids)
#
#         chart = ChartService.profit_trend(items_qs, params.get("filter"))
#
#         products = ProductService.top_products(date_from, date_to, store_ids)
#
#         return {
#             "filters": params,
#             "summary": summary,
#             "branchStatistics": branches,
#             "charts": {
#                 "profitTrend": chart
#             },
#             "topSellingProducts": products,
#             "debts": {
#                 "customerDebts": DebtService.customer_debt(store_ids),
#                 "supplierDebts": DebtService.supplier_debt()
#             }
#         }


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Report item querysetini select_related bilan barqaror qilish]
# ═══════════════════════════════


# ===================================================================================================
# ---------------------------------------------------------------------------------------------------
# ===================================================================================================
