from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import (
    Case,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import (
    Coalesce,
    Greatest,
    Least,
    TruncDay,
    TruncHour,
    TruncMonth,
    TruncWeek,
)
from django.utils import timezone

from apps.contract.models import SupplierTransaction
from apps.products.models import Product, ProductBatch
from apps.reports.services.reporting_foundation import ReportingFoundationService
from apps.sales.models import Payment, Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.store.models import Store

# ─────────────────────────────────────────────
#  Konstantalar
# ─────────────────────────────────────────────
LOW_STOCK_THRESHOLD  = 5    # shu miqdordan kam bo'lsa "kam qolgan"
RECENT_SALES_LIMIT   = 1
TOP_PARTS_LIMIT      = 5
LOW_STOCK_LIMIT      = 3

UZ_WEEKDAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
UZ_MONTHS   = [
    "Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
    "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr",
]


# ─────────────────────────────────────────────
#  DateRangeResolver
# ─────────────────────────────────────────────
@dataclass(frozen=True)
class DateRange:
    current_from: object
    current_to:   object
    prev_from:    object
    prev_to:      object


class DateRangeResolver:
    """
    period: 'daily' | 'weekly' | 'monthly' | 'yearly'
    Joriy va oldingi davr oralig'ini qaytaradi (growth hisoblash uchun).
    Custom (dan–gacha) oraliq uchun resolve_custom ishlatiladi.
    """

    @staticmethod
    def resolve(period: str) -> DateRange:
        now = timezone.now()

        if period == "daily":
            # Bugun 00:00 dan hozirgacha; taqqoslash — kecha to'liq kun
            today        = now.replace(hour=0, minute=0, second=0, microsecond=0)
            current_from = today
            current_to   = now
            prev_from    = today - timedelta(days=1)
            prev_to      = today
        elif period == "weekly":
            # Joriy haftaning Dushanbasi (weekday=0) — soat 00:00:00
            today        = now.replace(hour=0, minute=0, second=0, microsecond=0)
            current_from = today - timedelta(days=today.weekday())   # Dushanba
            current_to   = now
            # O'tgan hafta: bir hafta oldingi Dushanba–Yakshanba
            prev_from    = current_from - timedelta(weeks=1)
            prev_to      = current_from
        elif period == "monthly":
            delta        = timedelta(days=30)
            current_from = now - delta
            current_to   = now
            prev_from    = current_from - delta
            prev_to      = current_from
        else:                           # yearly
            current_from = now.replace(
                month=1,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            current_to   = now
            prev_from    = current_from.replace(year=current_from.year - 1)
            prev_to      = current_from

        return DateRange(
            current_from=current_from,
            current_to=current_to,
            prev_from=prev_from,
            prev_to=prev_to,
        )

    @staticmethod
    def resolve_custom(from_str: str, to_str: str) -> "DateRange | None":
        """
        'dan–gacha' oraliq (YYYY-MM-DD). Format noto'g'ri bo'lsa None.
        Growth taqqoslash uchun oldingi davr — xuddi shu uzunlikdagi avvalgi oraliq.
        """
        from datetime import datetime

        try:
            f = datetime.strptime(from_str, "%Y-%m-%d")
            t = datetime.strptime(to_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
        if t < f:
            f, t = t, f

        tz = timezone.get_current_timezone()
        current_from = timezone.make_aware(f.replace(hour=0, minute=0, second=0), tz)
        current_to   = timezone.make_aware(t.replace(hour=23, minute=59, second=59), tz)
        span         = current_to - current_from
        return DateRange(
            current_from=current_from,
            current_to=current_to,
            prev_from=current_from - span,
            prev_to=current_from,
        )


# ─────────────────────────────────────────────
#  Store scope yordamchi
# ─────────────────────────────────────────────
def _apply_store_filter(qs, store_id: str | None, store_field: str = "store_id"):
    """
    store_id='all' yoki None → filter yo'q.
    store_id='3'   → .filter(store_field=3)
    """
    if store_id and store_id != "all":
        return qs.filter(**{store_field: store_id})
    return qs


# ─────────────────────────────────────────────
#  KPI Service
# ─────────────────────────────────────────────
class KPIService:
    """
    Barcha KPI raqamlari + growth foizlari (Period Transactional Accounting).
    Sale va SaleReturn tranzaksiyalar oqimi orqali joriy va oldingi davrlar
    uchun mustaqil ravishda hisoblanadi.
    """

    @classmethod
    def _get_period_metrics(cls, start, end, store_id: str | None, inclusive_end: bool = False) -> dict:
        end_lookup = "lte" if inclusive_end else "lt"
        sales_filter = {
            "created_at__gte": start,
            f"created_at__{end_lookup}": end,
            "deleted_at__isnull": True,
        }
        sales_qs = Sale.objects.filter(**sales_filter)
        sales_qs = _apply_store_filter(sales_qs, store_id)

        sales_agg = sales_qs.aggregate(
            sold_revenue=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()),
            sold_paid=Coalesce(Sum("paid_amount"), Value(Decimal("0")), output_field=DecimalField()),
            sold_debt=Coalesce(
                Sum(
                    Case(
                        When(total_amount__gt=F("paid_amount"), then=F("total_amount") - F("paid_amount")),
                        default=Value(Decimal("0")),
                        output_field=DecimalField(),
                    )
                ),
                Value(Decimal("0")),
                output_field=DecimalField(),
            ),
            orders=Coalesce(Count("id"), Value(0)),
        )

        sold_revenue = sales_agg["sold_revenue"]
        sold_paid = sales_agg["sold_paid"]
        sold_debt = sales_agg["sold_debt"]
        orders = sales_agg["orders"]

        # Returns stream
        returns_filter = {
            "created_at__gte": start,
            f"created_at__{end_lookup}": end,
            "sale__deleted_at__isnull": True,
        }
        returns_qs = SaleReturn.objects.filter(**returns_filter)
        returns_qs = _apply_store_filter(returns_qs, store_id, store_field="store_id")

        parent_sale_debt = Greatest(Value(Decimal("0")), F("sale__total_amount") - F("sale__paid_amount"))
        debt_reduction_expr = Least(parent_sale_debt, F("total_refund"))

        returns_agg = returns_qs.aggregate(
            return_revenue=Coalesce(Sum("total_refund"), Value(Decimal("0")), output_field=DecimalField()),
            debt_reduced=Coalesce(Sum(debt_reduction_expr), Value(Decimal("0")), output_field=DecimalField()),
        )
        return_revenue = returns_agg["return_revenue"]
        calculated_debt_reduced = returns_agg["debt_reduced"]

        # Fallback to SaleReturnItem if total_refund is 0
        if return_revenue == Decimal("0"):
            ret_items_filter = {
                "sale_return__created_at__gte": start,
                f"sale_return__created_at__{end_lookup}": end,
                "sale_return__sale__deleted_at__isnull": True,
            }
            ret_items_qs = SaleReturnItem.objects.filter(**ret_items_filter)
            ret_items_qs = _apply_store_filter(ret_items_qs, store_id, store_field="sale_return__store_id")
            ret_items_agg = ret_items_qs.aggregate(
                total=Coalesce(Sum("total_price"), Value(Decimal("0")), output_field=DecimalField()),
                debt_red=Coalesce(
                    Sum(
                        Least(
                            Greatest(Value(Decimal("0")), F("sale_return__sale__total_amount") - F("sale_return__sale__paid_amount")),
                            F("total_price"),
                        )
                    ),
                    Value(Decimal("0")),
                    output_field=DecimalField(),
                ),
            )
            if ret_items_agg["total"] > Decimal("0"):
                return_revenue = ret_items_agg["total"]
                calculated_debt_reduced = ret_items_agg["debt_red"]

        # Check if actual cash/card refund payments exist in this period
        payments_filter = {
            "created_at__gte": start,
            f"created_at__{end_lookup}": end,
            "is_refund": True,
            "sale__deleted_at__isnull": True,
        }
        payments_refund_qs = Payment.objects.filter(**payments_filter)
        payments_refund_qs = _apply_store_filter(payments_refund_qs, store_id, store_field="sale__store_id")
        refund_paid_agg = payments_refund_qs.aggregate(
            total=Coalesce(Sum("amount"), Value(Decimal("0")), output_field=DecimalField())
        )
        refund_paid_payments = refund_paid_agg["total"]

        if refund_paid_payments > Decimal("0"):
            paid_refunded = refund_paid_payments
            debt_reduced = return_revenue - paid_refunded
        else:
            debt_reduced = calculated_debt_reduced
            paid_refunded = return_revenue - debt_reduced

        revenue = sold_revenue - return_revenue
        paid = sold_paid - paid_refunded
        debt = sold_debt - debt_reduced

        return {
            "revenue": revenue,
            "paid": paid,
            "debt": debt,
            "orders": orders,
        }

    @staticmethod
    def get(store_id: str | None, dr: DateRange) -> dict:
        cur = KPIService._get_period_metrics(dr.current_from, dr.current_to, store_id, inclusive_end=True)
        prev = KPIService._get_period_metrics(dr.prev_from, dr.prev_to, store_id, inclusive_end=False)

        cur_revenue  = cur["revenue"]
        cur_debt     = cur["debt"]
        cur_orders   = cur["orders"]
        prev_revenue = prev["revenue"]
        prev_debt    = prev["debt"]
        prev_orders  = prev["orders"]

        # lowStockCount — ProductBatch.quantity < threshold
        low_stock_qs = ProductBatch.objects.filter(
            quantity__lt=LOW_STOCK_THRESHOLD,
            is_active=True,
        )
        low_stock_qs = _apply_store_filter(low_stock_qs, store_id)
        low_stock_count = low_stock_qs.count()

        return {
            "revenue":        cur_revenue,
            "revenueGrowth":  _growth(cur_revenue,  prev_revenue),
            "paid":           cur["paid"],
            "debt":           cur_debt,
            "debtGrowth":     _growth(cur_debt,     prev_debt),
            "orders":         cur_orders,
            "ordersGrowth":   _growth(cur_orders,   prev_orders),
            "lowStockCount":  low_stock_count,
        }


def _growth(current: Decimal | int, previous: Decimal | int) -> float:
    """
    Foiz o'sish: ((cur - prev) / abs(prev)) * 100
    prev=0 bo'lsa: cur>0 → +100.0, cur<0 → -100.0, cur=0 → 0.0
    """
    if not previous:
        if current > 0:
            return 100.0
        elif current < 0:
            return -100.0
        return 0.0
    return round(float((current - previous) / abs(previous) * 100), 1)


# ─────────────────────────────────────────────
#  TopParts Service
# ─────────────────────────────────────────────
class TopPartsService:
    """
    Eng ko'p sotilgan TOP_PARTS_LIMIT ta mahsulot (Period Transactional Accounting).
    SaleItem va SaleReturnItem skalyar subquerylar orqali yagona SQL so'rovda hisoblanadi (Zero Cartesian Multiplication).
    """

    @staticmethod
    def get(store_id: str | None, dr: DateRange) -> list[dict]:
        # 1. SaleReturnItem subquerylari (quantity va revenue)
        returns_base = (
            SaleReturnItem.objects
            .filter(
                product_id=OuterRef("product_id"),
                sale_return__created_at__gte=dr.current_from,
                sale_return__created_at__lt=dr.current_to,
                sale_return__sale__deleted_at__isnull=True,
            )
        )
        returns_base = _apply_store_filter(returns_base, store_id, store_field="sale_return__store_id")

        ret_qty_subquery = (
            returns_base
            .annotate(dummy=Value(1))
            .values("dummy")
            .annotate(total=Sum("quantity"))
            .values("total")[:1]
        )

        ret_rev_subquery = (
            returns_base
            .annotate(dummy=Value(1))
            .values("dummy")
            .annotate(total=Sum("total_price"))
            .values("total")[:1]
        )

        # 2. SaleItem so'rovi (sotuvlar)
        sales_qs = (
            SaleItem.objects
            .filter(
                sale__created_at__gte=dr.current_from,
                sale__created_at__lt=dr.current_to,
                sale__deleted_at__isnull=True,
            )
        )
        sales_qs = _apply_store_filter(sales_qs, store_id, store_field="sale__store_id")

        # 3. Yagona SQL da agregatsiya, ayirish, filtrlash (sold > 0), saralash va LIMIT
        rows = (
            sales_qs
            .values("product_id", "product__name")
            .annotate(
                sold_qty=Coalesce(
                    Sum("quantity"),
                    Value(Decimal("0")),
                    output_field=DecimalField(),
                ),
                ret_qty=Coalesce(
                    Subquery(ret_qty_subquery, output_field=DecimalField()),
                    Value(Decimal("0")),
                    output_field=DecimalField(),
                ),
                sold_rev=Coalesce(
                    Sum("total_price"),
                    Value(Decimal("0")),
                    output_field=DecimalField(),
                ),
                ret_rev=Coalesce(
                    Subquery(ret_rev_subquery, output_field=DecimalField()),
                    Value(Decimal("0")),
                    output_field=DecimalField(),
                ),
            )
            .annotate(
                sold=ExpressionWrapper(
                    F("sold_qty") - F("ret_qty"),
                    output_field=DecimalField(),
                ),
                rev=ExpressionWrapper(
                    F("sold_rev") - F("ret_rev"),
                    output_field=DecimalField(),
                ),
            )
            .filter(sold__gt=0)
            .order_by("-sold", "product_id")
            [:TOP_PARTS_LIMIT]
        )

        return [
            {
                "id":   row["product_id"],
                "name": row["product__name"],
                "sold": row["sold"],
                "rev":  row["rev"],
            }
            for row in rows
        ]


# ─────────────────────────────────────────────
#  LowStock Service
# ─────────────────────────────────────────────
class LowStockService:
    """
    Omborda LOW_STOCK_THRESHOLD dan kam qolgan mahsulotlar.
    ProductBatch → product JOIN — bitta SQL.
    """

    @staticmethod
    def get(store_id: str | None) -> list[dict]:
        # ✅ filter → slice tartibida: Django slice'dan keyin filter qila olmaydi
        qs = (
            ProductBatch.objects
            .filter(quantity__lt=LOW_STOCK_THRESHOLD, is_active=True)
            .select_related("product")
            .only(
                "id", "quantity",
                "product_id", "product__name",
            )
            .order_by("quantity")
        )
        qs = _apply_store_filter(qs, store_id)
        qs = qs[:LOW_STOCK_LIMIT]

        return [
            {
                "id":       batch.id,
                "name":     batch.product.name,
                "quantity": batch.quantity,
            }
            for batch in qs
        ]


# ─────────────────────────────────────────────
#  RecentSales Service
# ─────────────────────────────────────────────
class RecentSalesService:
    """
    Oxirgi RECENT_SALES_LIMIT ta sotuv.
    Sale → customer, seller JOIN — bitta SQL, N+1 yo'q.
    time → minutelar soni (frontend o'zi formatlaydi).
    """

    @staticmethod
    def get(store_id: str | None) -> list[dict]:
        now = timezone.now()
        # ✅ filter → slice tartibida
        qs = (
            Sale.objects
            .select_related("customer", "seller")
            .only(
                "id", "total_amount", "status", "created_at",
                "customer__full_name",
                "seller__full_name",
            )
            .order_by("-created_at")
        )
        qs = _apply_store_filter(qs, store_id)
        qs = qs[:RECENT_SALES_LIMIT]

        return [
            {
                "id":     sale.id,
                "client": (
                    sale.customer.full_name
                    if sale.customer
                    else f"{sale.seller.full_name}".strip()
                ),
                "amount":      sale.total_amount,
                "minutesAgo":  max(0, int((now - sale.created_at).total_seconds() // 60)),
                "type":        sale.status,
            }
            for sale in qs
        ]


# ─────────────────────────────────────────────
#  Chart Service
# ─────────────────────────────────────────────
class ChartService:
    """
    daily   → 24 soat (00:00–23:00),          TruncHour
    weekly  → 7 kun (Dushanba–Yakshanba),     TruncDay
    monthly → 4 hafta (1-hafta .. 4-hafta),   TruncWeek
    yearly  → 12 oy (Yanvar–Dekabr),          TruncMonth
    custom  → oraliq uzunligiga qarab kunlik yoki oylik guruhlash

    Period Transactional Accounting:
    Sale (total_amount) va SaleReturn (total_refund) oqimlari mustaqil ravishda
    baza darajasida guruhlanadi va Python darajasida yagona kalit bo'yicha birlashtiriladi:
    net_value = sold - return.
    """

    @staticmethod
    def _get_return_rows(returns_qs, trunc_fn, dr: DateRange, store_id: str | None):
        ret_rows = (
            returns_qs.annotate(period=trunc_fn("created_at"))
            .values("period")
            .annotate(total=Coalesce(Sum("total_refund"), Value(Decimal("0")), output_field=DecimalField()))
            .order_by("period")
        )
        if returns_qs.exists() and not any(r["total"] > Decimal("0") for r in ret_rows):
            ret_items_qs = SaleReturnItem.objects.filter(
                sale_return__created_at__gte=dr.current_from,
                sale_return__created_at__lte=dr.current_to,
                sale_return__sale__deleted_at__isnull=True,
            )
            ret_items_qs = _apply_store_filter(ret_items_qs, store_id, store_field="sale_return__store_id")
            ret_items_rows = (
                ret_items_qs.annotate(period=trunc_fn("sale_return__created_at"))
                .values("period")
                .annotate(total=Coalesce(Sum("total_price"), Value(Decimal("0")), output_field=DecimalField()))
                .order_by("period")
            )
            if any(r["total"] > Decimal("0") for r in ret_items_rows):
                return ret_items_rows
        return ret_rows

    @staticmethod
    def get(store_id: str | None, dr: DateRange, period: str) -> dict:
        sales_qs = Sale.objects.filter(
            created_at__gte=dr.current_from,
            created_at__lte=dr.current_to,
            deleted_at__isnull=True,
        )
        sales_qs = _apply_store_filter(sales_qs, store_id)

        returns_qs = SaleReturn.objects.filter(
            created_at__gte=dr.current_from,
            created_at__lte=dr.current_to,
            sale__deleted_at__isnull=True,
        )
        returns_qs = _apply_store_filter(returns_qs, store_id, store_field="store_id")

        if period == "daily":
            return ChartService._daily(sales_qs, returns_qs, dr, store_id)
        elif period == "weekly":
            return ChartService._weekly(sales_qs, returns_qs, dr, store_id)
        elif period == "monthly":
            return ChartService._monthly(sales_qs, returns_qs, dr, store_id)
        elif period == "custom":
            return ChartService._custom(sales_qs, returns_qs, dr, store_id)
        else:
            return ChartService._yearly(sales_qs, returns_qs, dr, store_id)

    # ── daily (soatlik) ──
    @staticmethod
    def _daily(sales_qs, returns_qs, dr: DateRange, store_id: str | None) -> dict:
        sales_rows = (
            sales_qs.annotate(period=TruncHour("created_at"))
            .values("period")
            .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
            .order_by("period")
        )
        returns_rows = ChartService._get_return_rows(returns_qs, TruncHour, dr, store_id)

        sold_map = {timezone.localtime(r["period"]).hour: r["total"] for r in sales_rows}
        return_map = {timezone.localtime(r["period"]).hour: r["total"] for r in returns_rows}

        now_dt = timezone.localtime(dr.current_to) if timezone.is_aware(dr.current_to) else dr.current_to
        now_hour = now_dt.hour
        labels, values = [], []
        for hour in range(24):
            labels.append(f"{hour:02d}:00")
            if hour <= now_hour:
                net = sold_map.get(hour, Decimal("0")) - return_map.get(hour, Decimal("0"))
                values.append(net)
            else:
                values.append(None)

        return {"labels": labels, "data": values}

    # ── custom (dan–gacha) ──
    @staticmethod
    def _custom(sales_qs, returns_qs, dr: DateRange, store_id: str | None) -> dict:
        start = timezone.localtime(dr.current_from).date() if timezone.is_aware(dr.current_from) else dr.current_from.date()
        end   = timezone.localtime(dr.current_to).date() if timezone.is_aware(dr.current_to) else dr.current_to.date()
        span_days = (end - start).days + 1

        # 2 oygacha — kunlik nuqtalar; undan uzun — oylik guruhlash
        if span_days <= 62:
            sales_rows = (
                sales_qs.annotate(period=TruncDay("created_at"))
                .values("period")
                .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
                .order_by("period")
            )
            returns_rows = ChartService._get_return_rows(returns_qs, TruncDay, dr, store_id)

            sold_map = {
                (timezone.localtime(r["period"]).date() if timezone.is_aware(r["period"]) else r["period"].date()): r["total"]
                for r in sales_rows
            }
            return_map = {
                (timezone.localtime(r["period"]).date() if timezone.is_aware(r["period"]) else r["period"].date()): r["total"]
                for r in returns_rows
            }
            labels, values = [], []
            for i in range(span_days):
                day = start + timedelta(days=i)
                labels.append(day.strftime("%d.%m"))
                values.append(sold_map.get(day, Decimal("0")) - return_map.get(day, Decimal("0")))
            return {"labels": labels, "data": values}

        sales_rows = (
            sales_qs.annotate(period=TruncMonth("created_at"))
            .values("period")
            .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
            .order_by("period")
        )
        returns_rows = ChartService._get_return_rows(returns_qs, TruncMonth, dr, store_id)

        sold_map: dict[tuple[int, int], Decimal] = {}
        for r in sales_rows:
            p = timezone.localtime(r["period"]) if timezone.is_aware(r["period"]) else r["period"]
            sold_map[(p.year, p.month)] = sold_map.get((p.year, p.month), Decimal("0")) + r["total"]

        return_map: dict[tuple[int, int], Decimal] = {}
        for r in returns_rows:
            p = timezone.localtime(r["period"]) if timezone.is_aware(r["period"]) else r["period"]
            return_map[(p.year, p.month)] = return_map.get((p.year, p.month), Decimal("0")) + r["total"]

        multi_year = start.year != end.year
        labels, values = [], []
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            name = UZ_MONTHS[month - 1]
            labels.append(f"{name} {year}" if multi_year else name)
            net = sold_map.get((year, month), Decimal("0")) - return_map.get((year, month), Decimal("0"))
            values.append(net)
            month += 1
            if month > 12:
                month = 1
                year += 1
        return {"labels": labels, "data": values}

    # ── weekly ──
    @staticmethod
    def _weekly(sales_qs, returns_qs, dr: DateRange, store_id: str | None) -> dict:
        sales_rows = (
            sales_qs.annotate(period=TruncDay("created_at"))
            .values("period")
            .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
            .order_by("period")
        )
        returns_rows = ChartService._get_return_rows(returns_qs, TruncDay, dr, store_id)

        sold_map = {
            (timezone.localtime(r["period"]).date() if timezone.is_aware(r["period"]) else r["period"].date()): r["total"]
            for r in sales_rows
        }
        return_map = {
            (timezone.localtime(r["period"]).date() if timezone.is_aware(r["period"]) else r["period"].date()): r["total"]
            for r in returns_rows
        }

        today = timezone.localtime(dr.current_to).date() if timezone.is_aware(dr.current_to) else dr.current_to.date()
        monday = timezone.localtime(dr.current_from).date() if timezone.is_aware(dr.current_from) else dr.current_from.date()

        labels, values = [], []
        for i in range(7):
            day = monday + timedelta(days=i)
            labels.append(UZ_WEEKDAYS[day.weekday()])
            if day <= today:
                net = sold_map.get(day, Decimal("0")) - return_map.get(day, Decimal("0"))
                values.append(net)
            else:
                values.append(None)

        return {"labels": labels, "data": values}

    # ── monthly ──
    @staticmethod
    def _monthly(sales_qs, returns_qs, dr: DateRange, store_id: str | None) -> dict:
        sales_rows = (
            sales_qs.annotate(period=TruncDay("created_at"))
            .values("period")
            .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
            .order_by("period")
        )
        returns_rows = ChartService._get_return_rows(returns_qs, TruncDay, dr, store_id)

        cur_from_date = timezone.localtime(dr.current_from).date() if timezone.is_aware(dr.current_from) else dr.current_from.date()

        sold_weeks: dict[int, Decimal] = {}
        for row in sales_rows:
            p_date = timezone.localtime(row["period"]).date() if timezone.is_aware(row["period"]) else row["period"].date()
            week_num = min(4, max(1, ((p_date - cur_from_date).days // 7) + 1))
            sold_weeks[week_num] = sold_weeks.get(week_num, Decimal("0")) + row["total"]

        return_weeks: dict[int, Decimal] = {}
        for row in returns_rows:
            p_date = timezone.localtime(row["period"]).date() if timezone.is_aware(row["period"]) else row["period"].date()
            week_num = min(4, max(1, ((p_date - cur_from_date).days // 7) + 1))
            return_weeks[week_num] = return_weeks.get(week_num, Decimal("0")) + row["total"]

        labels = [f"{i}-hafta" for i in range(1, 5)]
        values = [sold_weeks.get(i, Decimal("0")) - return_weeks.get(i, Decimal("0")) for i in range(1, 5)]
        return {"labels": labels, "data": values}

    # ── yearly ──
    @staticmethod
    def _yearly(sales_qs, returns_qs, dr: DateRange, store_id: str | None) -> dict:
        now_dt = timezone.localtime(dr.current_to) if timezone.is_aware(dr.current_to) else dr.current_to
        current_year = now_dt.year
        now_month = now_dt.month

        sales_rows = (
            sales_qs.annotate(period=TruncMonth("created_at"))
            .values("period")
            .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
            .order_by("period")
        )
        returns_rows = ChartService._get_return_rows(returns_qs, TruncMonth, dr, store_id)

        sold_map: dict[int, Decimal] = {}
        for row in sales_rows:
            p = timezone.localtime(row["period"]) if timezone.is_aware(row["period"]) else row["period"]
            if p.year == current_year and p.month <= now_month:
                sold_map[p.month] = sold_map.get(p.month, Decimal("0")) + row["total"]

        return_map: dict[int, Decimal] = {}
        for row in returns_rows:
            p = timezone.localtime(row["period"]) if timezone.is_aware(row["period"]) else row["period"]
            if p.year == current_year and p.month <= now_month:
                return_map[p.month] = return_map.get(p.month, Decimal("0")) + row["total"]

        labels = UZ_MONTHS[:]
        values = [
            (sold_map.get(m, Decimal("0")) - return_map.get(m, Decimal("0"))) if m <= now_month else None
            for m in range(1, 13)
        ]
        return {"labels": labels, "data": values}

    yearly = _yearly


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 4  (Sale.created_at indekssiz aggregate — KPI/Chart/TopParts;
#                             ProductBatch.quantity indekssiz .count(); values bilan behuda select_related)
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Sale(store, created_at) va ProductBatch(store, quantity) indekslarini qo'shish]
# Eslatma: LowStockService va RecentSalesService only()+slice bilan namunali yozilgan (✅).
# ═══════════════════════════════