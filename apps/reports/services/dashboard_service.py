from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import (
    Case,
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
    TruncDay,
    TruncHour,
    TruncMonth,
    TruncWeek,
)
from django.utils import timezone

from apps.contract.models import SupplierTransaction
from apps.products.models import Product, ProductBatch
from apps.sales.models import Sale, SaleItem
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
            delta        = timedelta(days=365)
            current_from = now - delta
            current_to   = now
            prev_from    = current_from - delta
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
    Barcha KPI raqamlari + growth foizlari.
    Har bir metrik uchun FAQAT bitta aggregate query ishlatiladi.
    Joriy va oldingi davr bitta .aggregate() chaqiruvida hisoblanadi
    → jami 2 ta SQL (sales + supplier).
    """

    @staticmethod
    def get(store_id: str | None, dr: DateRange) -> dict:
        sales_qs = Sale.objects.filter(
            created_at__range=(dr.current_from, dr.current_to)
        )
        prev_sales_qs = Sale.objects.filter(
            created_at__range=(dr.prev_from, dr.prev_to)
        )
        sales_qs      = _apply_store_filter(sales_qs,      store_id)
        prev_sales_qs = _apply_store_filter(prev_sales_qs, store_id)

        # Joriy + oldingi davrni bitta aggregate'da emas,
        # Django ORM bitta queryset'da ikki range aggregate qila olmaydi —
        # shuning uchun ikkita query, lekin har biri bitta SQL.
        # ⚠️ MUAMMO [PERF]: cur/prev aggregate'lar Sale (65k qator) ustidan created_at__range
        # bilan ishlaydi. Sale.created_at (auto_now_add) ustida db_index YO'Q — natijada har
        # aggregate uchun sana oralig'ini topishda range scan og'irlashadi.
        # ✅ YECHIM: Sale modeliga index qo'yish, masalan:
        #   class Meta: indexes = [models.Index(fields=["store", "created_at"])]
        cur  = sales_qs.aggregate(
            revenue=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()),
            paid   =Coalesce(Sum("paid_amount"),  Value(Decimal("0")), output_field=DecimalField()),
            orders =Coalesce(Sum(Value(1), output_field=IntegerField()), Value(0)),
        )
        prev = prev_sales_qs.aggregate(
            revenue=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()),
            paid   =Coalesce(Sum("paid_amount"),  Value(Decimal("0")), output_field=DecimalField()),
            orders =Coalesce(Sum(Value(1), output_field=IntegerField()), Value(0)),
        )

        cur_revenue  = cur["revenue"]
        cur_debt     = cur["revenue"] - cur["paid"]
        cur_orders   = cur["orders"]
        prev_revenue = prev["revenue"]
        prev_debt    = prev["revenue"] - prev["paid"]
        prev_orders  = prev["orders"]

        # lowStockCount — ProductBatch.quantity < threshold
        low_stock_qs = ProductBatch.objects.filter(
            quantity__lt=LOW_STOCK_THRESHOLD,
            is_active=True,
        )
        low_stock_qs = _apply_store_filter(low_stock_qs, store_id)
        # ⚠️ MUAMMO [PERF]: .count() ProductBatch (12.5k) ustida quantity__lt=5 va is_active
        # filtri bilan ishlaydi. quantity ustida index YO'Q — COUNT uchun to'liq skan.
        # Bu KPI'da har chaqiruvda alohida SQL bo'ladi (aggregate'larga qo'shilmagan).
        # ✅ YECHIM: quantity/is_active ustiga composite index qo'yish
        #   models.Index(fields=["store", "quantity"]) — kam qolgan tovarlarni tez topish uchun.
        low_stock_count = low_stock_qs.count()

        return {
            "revenue":        cur_revenue,
            "revenueGrowth":  _growth(cur_revenue,  prev_revenue),
            "debt":           cur_debt,
            "debtGrowth":     _growth(cur_debt,     prev_debt),
            "orders":         cur_orders,
            "ordersGrowth":   _growth(cur_orders,   prev_orders),
            "lowStockCount":  low_stock_count,
        }


def _growth(current: Decimal | int, previous: Decimal | int) -> float:
    """
    Foiz o'sish: ((cur - prev) / prev) * 100
    prev=0 bo'lsa: cur>0 → +100.0, cur=0 → 0.0
    """
    if not previous:
        return 100.0 if current else 0.0
    return round(float((current - previous) / previous * 100), 1)


# ─────────────────────────────────────────────
#  TopParts Service
# ─────────────────────────────────────────────
class TopPartsService:
    """
    Eng ko'p sotilgan TOP_PARTS_LIMIT ta mahsulot.
    SaleItem → product JOIN — bitta SQL, N+1 yo'q.
    """

    @staticmethod
    def get(store_id: str | None, dr: DateRange) -> list[dict]:
        # ⚠️ MUAMMO [PERF]: .select_related("product") bu yerda BEHUDA — pastda .values()
        # ishlatilgani uchun ORM select_related JOIN'ini tashlab yuboradi (values o'zi kerakli
        # ustunlarni join qiladi). Chalkashlik keltiradi, real foyda bermaydi.
        # Bundan muhimi: filter SaleItem(65k) -> Sale JOIN orqali sale__created_at__range bo'yicha
        # boradi, Sale.created_at indekssiz — katta oraliqda sekin.
        # ✅ YECHIM: select_related'ni olib tashlash (values bilan keraksiz) + Sale.created_at index.
        qs = (
            SaleItem.objects
            .filter(sale__created_at__range=(dr.current_from, dr.current_to))
            .select_related("product")
        )
        qs = _apply_store_filter(qs, store_id, store_field="sale__store_id")

        rows = (
            qs
            .values("product_id", "product__name")
            .annotate(
                # DecimalField: quantity kasr bo'lishi mumkin (juft mahsulotda 0.5 qadam)
                sold=Coalesce(Sum("quantity"),   Value(0), output_field=DecimalField()),
                rev =Coalesce(Sum("total_price"), Value(Decimal("0")), output_field=DecimalField()),
            )
            .order_by("-sold")
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

    Barcha labellar to'ldiriladi — ma'lumot bo'lmagan kun/hafta/oy 0 bo'ladi.
    Bitta SQL query.
    """

    @staticmethod
    def get(store_id: str | None, dr: DateRange, period: str) -> dict:
        # ⚠️ MUAMMO [PERF]: Chart uchun Sale(65k) created_at__range bo'yicha filtrlanadi va
        # keyin TruncDay/TruncWeek/TruncMonth bilan GROUP BY qilinadi. created_at indekssiz +
        # Trunc funksiyasi ustidan guruhlash — katta oraliqda (yearly=365 kun) og'ir.
        # ✅ YECHIM: Sale.created_at index; guruhlash tez-tez bo'lsa oldindan agregatlangan
        # (kunlik jamlanma) jadval ko'rib chiqilsin.
        qs = Sale.objects.filter(
            created_at__range=(dr.current_from, dr.current_to)
        )
        qs = _apply_store_filter(qs, store_id)

        if period == "daily":
            return ChartService._daily(qs, dr)
        elif period == "weekly":
            return ChartService._weekly(qs, dr)
        elif period == "monthly":
            return ChartService._monthly(qs, dr)
        elif period == "custom":
            return ChartService._custom(qs, dr)
        else:
            return ChartService._yearly(qs, dr)

    # ── daily (soatlik) ──
    @staticmethod
    def _daily(qs, dr: DateRange) -> dict:
        rows = (
            qs.annotate(period=TruncHour("created_at"))
            .values("period")
            .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
            .order_by("period")
        )
        data_map = {timezone.localtime(row["period"]).hour: row["total"] for row in rows}

        now_hour = timezone.localtime(dr.current_to).hour
        labels, values = [], []
        for hour in range(24):
            labels.append(f"{hour:02d}:00")
            # Kelajak soatlar uchun None — frontend bo'sh ko'rsatadi
            values.append(data_map.get(hour, Decimal("0")) if hour <= now_hour else None)

        return {"labels": labels, "data": values}

    # ── custom (dan–gacha) ──
    @staticmethod
    def _custom(qs, dr: DateRange) -> dict:
        start = timezone.localtime(dr.current_from).date()
        end   = timezone.localtime(dr.current_to).date()
        span_days = (end - start).days + 1

        # 2 oygacha — kunlik nuqtalar; undan uzun — oylik guruhlash
        if span_days <= 62:
            rows = (
                qs.annotate(period=TruncDay("created_at"))
                .values("period")
                .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
                .order_by("period")
            )
            data_map = {timezone.localtime(row["period"]).date(): row["total"] for row in rows}
            labels, values = [], []
            for i in range(span_days):
                day = start + timedelta(days=i)
                labels.append(day.strftime("%d.%m"))
                values.append(data_map.get(day, Decimal("0")))
            return {"labels": labels, "data": values}

        rows = (
            qs.annotate(period=TruncMonth("created_at"))
            .values("period")
            .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
            .order_by("period")
        )
        data_map = {
            (timezone.localtime(row["period"]).year, timezone.localtime(row["period"]).month): row["total"]
            for row in rows
        }
        multi_year = start.year != end.year
        labels, values = [], []
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            name = UZ_MONTHS[month - 1]
            labels.append(f"{name} {year}" if multi_year else name)
            values.append(data_map.get((year, month), Decimal("0")))
            month += 1
            if month > 12:
                month = 1
                year += 1
        return {"labels": labels, "data": values}

    # ── weekly ──
    @staticmethod
    def _weekly(qs, dr: DateRange) -> dict:
        rows = (
            qs.annotate(period=TruncDay("created_at"))
            .values("period")
            .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
            .order_by("period")
        )
        data_map = {row["period"].date(): row["total"] for row in rows}

        today = dr.current_to.date()
        monday = dr.current_from.date()

        labels, values = [], []
        for i in range(7):
            day = monday + timedelta(days=i)
            labels.append(UZ_WEEKDAYS[day.weekday()])
            # Kelajak kunlar uchun None — frontend bo'sh ko'rsatadi
            values.append(data_map.get(day, Decimal("0")) if day <= today else None)

        return {"labels": labels, "data": values}

    # ── monthly ──
    @staticmethod
    def _monthly(qs, dr: DateRange) -> dict:
        rows = (
            qs.annotate(period=TruncWeek("created_at"))
            .values("period")
            .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
            .order_by("period")
        )
        # Haftalarni tartib bo'yicha 1-hafta, 2-hafta ... ga moslaymiz
        week_totals: dict[int, Decimal] = {}
        for row in rows:
            week_num = ((row["period"].date() - dr.current_from.date()).days // 7) + 1
            if 1 <= week_num <= 4:
                week_totals[week_num] = week_totals.get(week_num, Decimal("0")) + row["total"]

        labels = [f"{i}-hafta" for i in range(1, 5)]
        values = [week_totals.get(i, Decimal("0")) for i in range(1, 5)]
        return {"labels": labels, "data": values}

    # ── yearly ──
    @staticmethod
    def _yearly(qs, dr: DateRange) -> dict:
        rows = (
            qs.annotate(period=TruncMonth("created_at"))
            .values("period")
            .annotate(total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()))
            .order_by("period")
        )
        data_map = {row["period"].month: row["total"] for row in rows}

        labels = UZ_MONTHS[:]
        values = [data_map.get(m, Decimal("0")) for m in range(1, 13)]
        return {"labels": labels, "data": values}


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