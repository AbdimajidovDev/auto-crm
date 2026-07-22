# from .summary_service import SummaryService
# from .branch_service import BranchService
# from .debt_service import DebtService
# from .filter_service import ReportFilterService


# ===========================   Reports   ==================================================

from __future__ import annotations

from datetime import date, datetime, time, timedelta
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

from apps.contract.models import StockEntryPayment, SupplierTransaction
from apps.sales.models import BankCard, Payment, Sale, SaleItem, SaleReturn
from apps.store.models import Store
from ...debts.models import CustomerDebt

# ─────────────────────────────────────────────
#  Konstantalar
# ─────────────────────────────────────────────
TOP_PRODUCTS_LIMIT = 5

PAYMENT_METHOD_LABELS = {
    "cash": "Naqd",
    "card": "Karta",
    "mixed": "Aralash",
    "debt": "Qarz",
}

# Eski (bank_card=NULL) karta to'lovlari hisobotda shu nom bilan chiqadi
UNKNOWN_CARD_LABEL = "Noma'lum karta"


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
        if filter_type == "daily":
            # Bugungi kun (00:00 dan hozirgacha — _dt_bounds to'liq kunni qamraydi)
            return today, today
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


def _dt_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """
    Sana oralig'ini [start, end) yarim-ochiq datetime chegaralarga aylantiradi.

    `created_at__date__gte/lte` SQL'da ustunni DATE(...)ga o'rab, indeksni
    ishlatib bo'lmaydigan (non-sargable) qilib qo'yardi. Xom datetime chegaralar
    bilan filtr indeksdan to'liq foydalanadi — natija aynan bir xil:
    date_to kunining oxirigacha (23:59:59.999999) qamrab olinadi.
    """
    start = datetime.combine(date_from, time.min)
    end = datetime.combine(date_to + timedelta(days=1), time.min)
    if timezone.is_naive(start):
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(start, tz)
        end = timezone.make_aware(end, tz)
    return start, end


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
    # Sargable filtr: xom datetime chegaralar bilan `created_at` indeksi to'liq ishlaydi
    # (avvalgi `__date__gte/lte` DATE(created_at) o'ramasi indeksni o'chirib qo'yardi).
    start, end = _dt_bounds(date_from, date_to)
    return (
        Sale.objects
        .filter(created_at__gte=start, created_at__lt=end)
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
        start, end = _dt_bounds(date_from, date_to)
        items_qs = (
            SaleItem.objects
            .filter(
                sale__created_at__gte=start,
                sale__created_at__lt=end,
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
        start, end = _dt_bounds(date_from, date_to)
        qs = (
            SaleItem.objects
            .filter(
                sale__created_at__gte=start,
                sale__created_at__lt=end,
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
        start, end = _dt_bounds(date_from, date_to)
        rows = (
            SaleItem.objects
            .filter(
                sale__created_at__gte=start,
                sale__created_at__lt=end,
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
    To'lov turlari bo'yicha taqsimot: Naqd / Karta / Aralash / Qarz.

    Sale.payment_type (backend avtomatik hisoblaydigan maydon) bo'yicha guruhlanadi —
    shu tufayli ARALASH (naqd + karta) sotuvlar alohida qatorda ko'rinadi.
    Amount sifatida real tushgan pul (paid_amount) olinadi. 1 ta SQL + qarz uchun 1 ta.
    """

    @staticmethod
    def get(date_from: date, date_to: date, store_id: int | None) -> list[dict]:
        start, end = _dt_bounds(date_from, date_to)
        qs = (
            Sale.objects
            .filter(created_at__gte=start, created_at__lt=end)
            .filter(_store_q(store_id))
            .exclude(payment_type=Sale.PaymentType.DEBT)  # pulsiz sotuvlar Qarz qatorida
            .values("payment_type")
            .annotate(
                count=Count("id"),
                amount=Coalesce(
                    Sum("paid_amount"),
                    Value(Decimal("0")), output_field=DecimalField(),
                ),
            )
            .order_by("-amount")
        )

        rows = list(qs)

        # Qarz — to'lanmagan qoldiq (to'liq qarzdagi va qisman to'langan sotuvlar)
        debt_agg = (
            Sale.objects
            .filter(
                created_at__gte=start,
                created_at__lt=end,
                status__in=[Sale.Status.DEBT, Sale.Status.PARTIAL],
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

        total_amount = (
            sum(r["amount"] for r in rows) + (debt_agg["amount"] or Decimal("0"))
        ) or Decimal("1")

        result = [
            {
                "method":  PAYMENT_METHOD_LABELS.get(r["payment_type"], r["payment_type"]),
                "type":    r["payment_type"],
                "count":   r["count"],
                "amount":  r["amount"],
                "percent": f"{round(float(r['amount'] / total_amount * 100), 1)}%",
            }
            for r in rows
        ]

        if debt_agg["amount"]:
            result.append({
                "method":  PAYMENT_METHOD_LABELS["debt"],
                "type":    "debt",
                "count":   debt_agg["count"],
                "amount":  debt_agg["amount"],
                "percent": f"{round(float(debt_agg['amount'] / total_amount * 100), 1)}%",
            })

        return result


# ─────────────────────────────────────────────
#  Bank kartalari kesimi
# ─────────────────────────────────────────────
class BankCardBreakdownService:
    """
    Har bir bank kartasi bo'yicha NET tushum (karta to'lovlari - karta qaytarimlari).
    Eski to'lovlarda bank_card=NULL bo'lishi mumkin — "Noma'lum karta" bo'lib chiqadi.
    1 ta SQL.
    """

    @staticmethod
    def get(date_from: date, date_to: date, store_id: int | None) -> list[dict]:
        start, end = _dt_bounds(date_from, date_to)
        qs = (
            Payment.objects
            .filter(
                type=Payment.Type.CARD,
                created_at__gte=start,
                created_at__lt=end,
            )
            .filter(_store_q(store_id, "sale__store_id"))
            .values("bank_card_id", "bank_card__name")
            .annotate(
                count=Count("id"),
                amount=Coalesce(
                    Sum(
                        Case(
                            When(is_refund=True, then=-F("amount")),
                            default=F("amount"),
                            output_field=DecimalField(),
                        )
                    ),
                    Value(Decimal("0")), output_field=DecimalField(),
                ),
            )
            .order_by("-amount")
        )

        rows = list(qs)
        by_card_id = {r["bank_card_id"]: r for r in rows}

        # Barcha faol sotuv kartalari ro'yxatga kiradi — to'lovi bo'lmaganlari 0 bilan
        # ("humodan shuncha, uzcarddan shuncha" har doim to'liq ko'rinadi)
        result = []
        for card in BankCard.objects.filter(
            is_active=True,
            scope__in=[BankCard.Scope.SALE, BankCard.Scope.BOTH],
        ).values("id", "name"):
            r = by_card_id.pop(card["id"], None)
            result.append({
                "bankCardId": card["id"],
                "name":       card["name"],
                "count":      r["count"] if r else 0,
                "amount":     r["amount"] if r else Decimal("0"),
            })

        # Qolganlari: nofaol kartalar yoki bank_card=NULL bo'lgan eski to'lovlar
        for r in by_card_id.values():
            result.append({
                "bankCardId": r["bank_card_id"],
                "name":       r["bank_card__name"] or UNKNOWN_CARD_LABEL,
                "count":      r["count"],
                "amount":     r["amount"],
            })

        result.sort(key=lambda r: r["amount"], reverse=True)
        total_amount = sum(r["amount"] for r in result) or Decimal("1")
        for r in result:
            r["percent"] = f"{round(float(r['amount'] / total_amount * 100), 1)}%"

        return result


# ─────────────────────────────────────────────
#  Chiqimlar (davr ichida chiqib ketgan pul)
# ─────────────────────────────────────────────
class ExpensesService:
    """
    Davr ichidagi chiqimlar — to'lov usuli/karta kesimida to'liq:
      - Mijozlarga qaytarimlar (naqd va karta alohida, Payment.is_refund=True)
      - Ta'minotchilarga qarz to'lovlari (SupplierTransaction type='pay') —
        naqd / har bir karta (Humo, Uzcard, ...) alohida qatorda
      - Xarid (kirim) paytidagi to'lovlar (StockEntryPayment) — naqd / har bir
        karta alohida qatorda
    3 ta SQL.
    """

    @staticmethod
    def _method_rows(qs_rows, *, base_label: str, base_type: str) -> list[dict]:
        """
        payment_method/type + bank_card bo'yicha guruhlangan qatorlarni hisobot
        formatiga o'tkazadi: naqd bitta qator, har bir karta alohida qator.
        Kutiladigan kalitlar: method ('cash'|'card'|''), bank_card_id,
        bank_card_name, count, amount.
        """
        rows = []
        for r in qs_rows:
            if not r["amount"]:
                continue
            method = r["method"]
            if method == "card":
                card_name = r["bank_card_name"] or UNKNOWN_CARD_LABEL
                rows.append({
                    "method": f"{base_label} ({card_name})",
                    "type":   f"{base_type}_card_{r['bank_card_id'] or 0}",
                    "count":  r["count"],
                    "amount": r["amount"],
                })
            elif method == "cash":
                rows.append({
                    "method": f"{base_label} (naqd)",
                    "type":   f"{base_type}_cash",
                    "count":  r["count"],
                    "amount": r["amount"],
                })
            else:
                # Eski yozuvlar: payment_method kiritilmagan
                rows.append({
                    "method": base_label,
                    "type":   base_type,
                    "count":  r["count"],
                    "amount": r["amount"],
                })
        return rows

    @staticmethod
    def get(date_from: date, date_to: date, store_id: int | None) -> list[dict]:
        start, end = _dt_bounds(date_from, date_to)
        # Mijozga qaytarimlar — usul + karta kesimida (naqd / har bir karta alohida)
        refunds = (
            Payment.objects
            .filter(
                is_refund=True,
                created_at__gte=start,
                created_at__lt=end,
            )
            .filter(_store_q(store_id, "sale__store_id"))
            .values("type", "bank_card_id", "bank_card__name")
            .annotate(
                count=Count("id"),
                amount=Coalesce(Sum("amount"), Value(Decimal("0")), output_field=DecimalField()),
            )
        )

        # Ta'minotchilarga qarz to'lovlari — usul + karta kesimida
        supplier_pay = (
            SupplierTransaction.objects
            .filter(
                type=SupplierTransaction.TransactionType.PAYMENT,
                created_at__gte=start,
                created_at__lt=end,
            )
            .filter(_store_q(store_id, "entry__store_id"))
            .values("payment_method", "bank_card_id", "bank_card__name")
            .annotate(
                count=Count("id"),
                amount=Coalesce(Sum("amount"), Value(Decimal("0")), output_field=DecimalField()),
            )
        )

        # Xarid (kirim) paytida to'langan pullar — usul + karta kesimida
        purchase_pay = (
            StockEntryPayment.objects
            .filter(
                created_at__gte=start,
                created_at__lt=end,
            )
            .filter(_store_q(store_id, "entry__store_id"))
            .values("type", "bank_card_id", "bank_card__name")
            .annotate(
                count=Count("id"),
                amount=Coalesce(Sum("amount"), Value(Decimal("0")), output_field=DecimalField()),
            )
        )

        rows = ExpensesService._method_rows(
            (
                {
                    "method": r["type"],
                    "bank_card_id": r["bank_card_id"],
                    "bank_card_name": r["bank_card__name"],
                    "count": r["count"],
                    "amount": r["amount"],
                }
                for r in refunds
            ),
            base_label="Mijozga qaytarim",
            base_type="refund",
        )

        rows += ExpensesService._method_rows(
            (
                {
                    "method": r["payment_method"],
                    "bank_card_id": r["bank_card_id"],
                    "bank_card_name": r["bank_card__name"],
                    "count": r["count"],
                    "amount": r["amount"],
                }
                for r in supplier_pay
            ),
            base_label="Ta'minotchilarga to'lov",
            base_type="supplier_payment",
        )

        rows += ExpensesService._method_rows(
            (
                {
                    "method": r["type"],
                    "bank_card_id": r["bank_card_id"],
                    "bank_card_name": r["bank_card__name"],
                    "count": r["count"],
                    "amount": r["amount"],
                }
                for r in purchase_pay
            ),
            base_label="Xarid to'lovi",
            base_type="purchase_payment",
        )

        rows.sort(key=lambda r: r["amount"], reverse=True)
        total = sum(r["amount"] for r in rows) or Decimal("1")
        for r in rows:
            r["percent"] = f"{round(float(r['amount'] / total * 100), 1)}%"

        return rows


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
            "cardBreakdown":      BankCardBreakdownService.get(date_from, date_to, store_id),
            "expenses":           ExpensesService.get(date_from, date_to, store_id),
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
