"""
Universal hisobot quruvchi (Reports moduli).

Arxitektura:
  - REPORTS registry: har hisobot turi = kalit + label + filtr sxemasi + builder.
  - Meta endpoint frontendga hisobot turlari va ularning DINAMIK filtrlarini
    (variantlari bilan) beradi — frontend hech narsani hardcode qilmaydi.
  - Generate: tanlangan filtrlar bilan server tomonida filtrlangan jadval
    (ustunlar + qatorlar + jami/summary kartalar) + pagination.
  - Export (excel/csv) AYNAN o'sha filtrlar bilan bir xil yo'ldan quriladi —
    jadval bilan fayl hech qachon farq qilmaydi.

Do'kon cheklovi view qatlamida (scope_report_params) qo'llanadi: do'kon
admini faqat o'z do'koni bo'yicha hisobot oladi.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import (
    Case, Count, DecimalField, Exists, ExpressionWrapper, F, IntegerField, OuterRef,
    Q, Subquery, Sum, Value, When,
)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.contract.models import StockEntryItem, Supplier, SupplierTransaction
from apps.debts.models import CustomerDebt
from apps.products.models import Category, Product, ProductBatch
from apps.products.services.product_history_service import parse_date_param
from apps.reports.services.product_movement_report_service import ProductMovementReportService
from apps.products.services.product_query_service import (
    LOW_STOCK_THRESHOLD,
    annotate_stock_qty,
    apply_stock_status,
    apply_token_search,
)
from apps.sales.models import BankCard, Payment, Sale, SaleItem
from apps.sales.profit import partial_cost_filter, sum_item_profit
from apps.store.models import Store
from apps.users.models.customers import Customer

from .report_service import _dt_bounds, _store_q, ExpensesService
from .stock_history_service import day_end, stock_delta_after

# Eksportda ham cheklov bor — "hamma yozuvlar" hech qachon yuklanmaydi
EXPORT_MAX_ROWS = 5000
# Katta kataloglar (qoldiqlar, ta'minotchi sotuvlari) uchun kengaytirilgan cap —
# registry'da export_cap orqali tanlanadi
LARGE_EXPORT_CAP = 50_000
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

PAYMENT_TYPE_LABELS = {"cash": "Naqd", "card": "Karta", "mixed": "Aralash", "debt": "Qarz"}
SALE_STATUS_LABELS = {"paid": "To'langan", "partial": "Qisman", "debt": "Qarz", "r": "Qaytarilgan"}

# ── Mahsulot tarixi hisoboti ──
PRODUCT_EVENT_LABELS = {
    "entry": "Kirim",
    "entry_return": "Kirim qaytimi",
    "transfer": "O'tkazma",
    "sale": "Sotuv",
    "sale_return": "Sotuv qaytimi",
    "writeoff": "Spisaniye",
    "inventory": "Inventarizatsiya",
}
# Hodisa holati har manbada boshqacha kodlanadi (o'tkazma / sotuv / spisaniye /
# inventarizatsiya) — jadvalda o'qiladigan matn chiqishi uchun bir joyga yig'ildi
PRODUCT_EVENT_STATUS_LABELS = {
    "transfer": {"p": "Kutilmoqda", "a": "Tasdiqlangan", "r": "Rad etilgan"},
    "sale": SALE_STATUS_LABELS,
    "writeoff": {
        "damaged": "Buzilgan / yaroqsiz",
        "expired": "Muddati o'tgan",
        "lost": "Yo'qolgan / o'g'irlangan",
        "inventory": "Inventarizatsiya kamomadi",
        "catalog": "Katalogdan chiqarish",
        "other": "Boshqa",
    },
    "inventory": {"active": "Faol", "completed": "Yakunlangan", "cancelled": "Bekor qilingan"},
}
# Bitta mahsulot uchun birlashtiriladigan hodisalar chegarasi. Lenta 7 xil
# jadvaldan yig'ilib Python'da saralanadi — chegarasiz eng faol mahsulotda
# o'n minglab qator xotiraga ko'tarilardi. Cap urilsa foydalanuvchi
# ogohlantiriladi (jimgina kesish yo'q).
PRODUCT_HISTORY_MAX_EVENTS = 2000


# ─────────────────────────────────────────────
#  Param parsing yordamchilari
# ─────────────────────────────────────────────
# Sana tanlanmaganda "boshidan" chegarasi. Aniq sana kerak, chunki filtr
# indeksdan foydalanadigan (sargable) oraliq bo'lib qolishi shart.
ALL_TIME_START = date(2000, 1, 1)


def _one_date(raw: str, field: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValidationError({field: "ISO format: YYYY-MM-DD"})


def _parse_dates(params, default_all: bool = False) -> tuple[date, date]:
    """
    from/to (ISO) sana oralig'i.

    Bittasi berilmasa ham hisobot chiqadi:
      - faqat from  → o'sha kundan bugungacha
      - faqat to    → boshidan o'sha kungacha
      - ikkalasi yo'q → default_all bo'lsa BOSHIDAN bugungacha (jami),
                        aks holda oxirgi 30 kun
    """
    today = timezone.localdate()
    from_raw = (params.get("from") or "").strip()
    to_raw = (params.get("to") or "").strip()

    if from_raw and to_raw:
        return _one_date(from_raw, "from"), _one_date(to_raw, "to")
    if from_raw:
        return _one_date(from_raw, "from"), today
    if to_raw:
        return ALL_TIME_START, _one_date(to_raw, "to")
    # Sana umuman tanlanmagan
    if default_all:
        return ALL_TIME_START, today
    return today - timedelta(days=30), today


def _period_label(params, d_from: date, d_to: date) -> str:
    """
    Hisobot qaysi davrni qamraganini foydalanuvchiga aytadi — sana tanlanmagan
    holatda "jami" ekani ko'rinib tursin (raqamlar sirli bo'lib qolmasin).
    """
    from_given = bool((params.get("from") or "").strip())
    to_given = bool((params.get("to") or "").strip())
    to_label = d_to.strftime("%d.%m.%Y")
    if not from_given and d_from == ALL_TIME_START:
        return f"Boshidan {to_label} gacha (jami)"
    return f"{d_from.strftime('%d.%m.%Y')} — {to_label}" if to_given or from_given else to_label


def _parse_as_of(params) -> date | None:
    """
    as_of (ISO) — qoldiq holati SHU KUN OXIRIGA hisoblanadi ("boshidan shu
    kungacha"). Berilmasa — joriy (bugungi) holat.
    """
    raw = (params.get("as_of") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValidationError({"as_of": "ISO format: YYYY-MM-DD"})


def _parse_store(params) -> int | None:
    raw = params.get("store_id")
    if not raw or raw == "all":
        return None
    if not str(raw).isdigit():
        raise ValidationError({"store_id": "Noto'g'ri qiymat"})
    return int(raw)


def _parse_int(params, key, default, allowed=None) -> int:
    raw = params.get(key)
    if raw is None or raw == "":
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    if allowed and val not in allowed:
        return default
    return val


def _money(v) -> str:
    return f"{Decimal(str(v or 0)):.2f}"


# ─────────────────────────────────────────────
#  Umumiy filtr sxemalari (meta uchun)
# ─────────────────────────────────────────────
def _f_daterange():
    return {"param": "date", "type": "daterange", "label": "Sana oralig'i"}


def _f_store():
    options = [{"value": "all", "label": "Barcha do'konlar"}] + [
        {"value": str(s["id"]), "label": s["name"]}
        for s in Store.objects.filter(is_active=True).values("id", "name").order_by("name")
    ]
    return {"param": "store_id", "type": "select", "label": "Do'kon / sklad", "options": options}


def _f_category():
    options = [{"value": "", "label": "Barcha kategoriyalar"}] + [
        {"value": str(c["id"]), "label": c["name"]}
        for c in Category.objects.values("id", "name").order_by("name")
    ]
    return {"param": "category_id", "type": "select", "label": "Kategoriya", "options": options}


def _f_date(param, label):
    return {"param": param, "type": "date", "label": label}


def _f_select(param, label, pairs, empty_label=None):
    options = ([{"value": "", "label": empty_label}] if empty_label else []) + [
        {"value": v, "label": l} for v, l in pairs
    ]
    return {"param": param, "type": "select", "label": label, "options": options}


def _f_product():
    """
    Bitta mahsulot tanlash. Katalog minglab qatorli — variantlar meta bilan
    yuborilmaydi, frontend qidiruv (autocomplete) orqali tanlaydi.
    """
    return {"param": "product_id", "type": "product", "label": "Mahsulot", "required": True}


def _f_supplier():
    options = [{"value": "", "label": "Barcha ta'minotchilar"}] + [
        {"value": str(s["id"]), "label": s["name"]}
        for s in Supplier.objects.filter(is_active=True).values("id", "name").order_by("name")
    ]
    return {"param": "supplier_id", "type": "select", "label": "Yetkazib beruvchi", "options": options}


# ─────────────────────────────────────────────
#  BUILDERLAR — har biri (columns, rows, summary) qaytaradi
#  rows: dict ro'yxati (column key → qiymat)
# ─────────────────────────────────────────────
def _build_sales(params, store_id):
    # Sana tanlanmasa — boshidan bugungacha JAMI (foydalanuvchi "hammasi"ni
    # ko'rish uchun har safar sana tanlab o'tirmasin)
    d_from, d_to = _parse_dates(params, default_all=True)
    start, end = _dt_bounds(d_from, d_to)
    # Har chek uchun sof foyda — Subquery (JOIN emas): items bo'yicha aggregate
    # asosiy queryset qatorlarini ko'paytirib, jami summalarni buzib yuborardi
    profit_sq = (
        SaleItem.objects
        .filter(sale=OuterRef("pk"))
        .values("sale")
        .annotate(total=sum_item_profit())
        .values("total")[:1]
    )
    qs = (
        Sale.objects
        .filter(created_at__gte=start, created_at__lt=end)
        .filter(_store_q(store_id))
        .select_related("store", "customer", "seller")
        .annotate(
            net_profit=Coalesce(
                Subquery(profit_sq, output_field=DecimalField()),
                Value(Decimal("0")),
                output_field=DecimalField(),
            )
        )
    )
    payment_type = params.get("payment_type")
    if payment_type in PAYMENT_TYPE_LABELS:
        qs = qs.filter(payment_type=payment_type)
    sale_status = params.get("status")
    if sale_status in SALE_STATUS_LABELS:
        qs = qs.filter(status=sale_status)
    search = (params.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(customer__full_name__icontains=search)
            | Q(customer__phone_number__icontains=search)
            | (Q(id__iexact=search) if search.isdigit() else Q())
        )
    qs = qs.order_by("-created_at")

    agg = qs.aggregate(
        n=Count("id"),
        total=Coalesce(Sum("total_amount"), Value(Decimal("0")), output_field=DecimalField()),
        paid=Coalesce(Sum("paid_amount"), Value(Decimal("0")), output_field=DecimalField()),
        profit=Coalesce(Sum("net_profit"), Value(Decimal("0")), output_field=DecimalField()),
    )
    columns = [
        {"key": "id", "label": "Chek №", "kind": "int"},
        {"key": "date", "label": "Sana", "kind": "text"},
        {"key": "store", "label": "Do'kon", "kind": "text"},
        {"key": "customer", "label": "Mijoz", "kind": "text"},
        {"key": "seller", "label": "Sotuvchi", "kind": "text"},
        {"key": "total", "label": "Jami", "kind": "money"},
        {"key": "paid", "label": "To'langan", "kind": "money"},
        {"key": "debt", "label": "Qarz", "kind": "money"},
        {"key": "profit", "label": "Sof foyda", "kind": "money"},
        {"key": "payment", "label": "To'lov turi", "kind": "text"},
        {"key": "status", "label": "Holat", "kind": "text"},
    ]

    def row(s):
        return {
            "id": s.id,
            "date": timezone.localtime(s.created_at).strftime("%d.%m.%Y %H:%M"),
            "store": s.store.name if s.store else "-",
            "customer": s.customer.full_name if s.customer else "-",
            "seller": (s.seller.full_name or "-") if s.seller else "-",
            "total": _money(s.total_amount),
            "paid": _money(s.paid_amount),
            "debt": _money((s.total_amount or 0) - (s.paid_amount or 0)),
            "profit": _money(getattr(s, "net_profit", 0)),
            "payment": PAYMENT_TYPE_LABELS.get(s.payment_type, s.payment_type),
            "status": SALE_STATUS_LABELS.get(s.status, s.status),
        }

    revenue = agg["total"]
    margin = (agg["profit"] / revenue * 100) if revenue else Decimal("0")
    summary = [
        {"label": "Davr", "value": _period_label(params, d_from, d_to), "kind": "text"},
        {"label": "Sotuvlar soni", "value": agg["n"], "kind": "int"},
        {"label": "Jami summa", "value": _money(agg["total"]), "kind": "money"},
        {"label": "To'langan", "value": _money(agg["paid"]), "kind": "money"},
        {"label": "Qarz", "value": _money(agg["total"] - agg["paid"]), "kind": "money"},
        {"label": "Sof foyda", "value": _money(agg["profit"]), "kind": "money"},
        {"label": "Marja", "value": f"{margin:.1f}%", "kind": "text"},
    ]
    # Tannarxi yo'q sotuvlar bo'lsa foyda oshiq ko'rinadi — jimgina o'tkazmaymiz
    if SaleItem.objects.filter(
        sale__in=qs.values("id"),
    ).filter(partial_cost_filter()).exists():
        summary.append({
            "label": "Diqqat",
            "value": "Ba'zi sotuvlarda tannarx yo'q — foyda taxminiy",
            "kind": "text",
        })
    return columns, qs, row, summary


def _build_top_products(params, store_id):
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)
    top_n = _parse_int(params, "top", 20, allowed={10, 20, 50, 100})
    sort_by = params.get("sort_by") if params.get("sort_by") in ("quantity", "revenue", "profit") else "revenue"

    qs = (
        SaleItem.objects
        .filter(sale__created_at__gte=start, sale__created_at__lt=end)
        .filter(_store_q(store_id, "sale__store_id"))
        .exclude(sale__status=Sale.Status.RETURNED)
    )
    category_id = params.get("category_id")
    if category_id and str(category_id).isdigit():
        qs = qs.filter(product__category_id=int(category_id))
    # Ta'minotchi bo'yicha: shu ta'minotchidan kirim qilingan mahsulotlargina
    qs = _supplied_by_filter(qs, params)

    # Diqqat: annotatsiya nomlari model maydonlari (quantity) bilan to'qnashmasligi
    # shart — aks holda F("quantity") aggregatga ishora qilib FieldError beradi
    grouped = (
        qs.values("product_id", "product__name", "product__sku", "product__category__name")
        .annotate(
            # DecimalField: quantity kasr bo'lishi mumkin (juft mahsulotda 0.5 qadam)
            sold_qty=Coalesce(
                Sum(ExpressionWrapper(
                    F("quantity") - F("returned_quantity"), output_field=DecimalField(),
                )),
                Value(0), output_field=DecimalField(),
            ),
            revenue_sum=Coalesce(
                Sum(ExpressionWrapper(
                    F("unit_price") * (F("quantity") - F("returned_quantity")),
                    output_field=DecimalField(),
                )),
                Value(Decimal("0")), output_field=DecimalField(),
            ),
            # Sof foyda — sotuvlar hisoboti bilan aynan bir formula
            profit_sum=sum_item_profit(),
        )
        .filter(sold_qty__gt=0)
        .order_by(
            "-sold_qty" if sort_by == "quantity"
            else ("-profit_sum" if sort_by == "profit" else "-revenue_sum")
        )[:top_n]
    )
    rows_raw = list(grouped)
    # Har mahsulotning (oxirgi kirimdagi) ta'minotchisi — qaysi ta'minotchidan
    # kelayotgan mahsulotlar ko'proq sotilayotganini ko'rsatadi
    smap = _last_supplier_map({r["product_id"] for r in rows_raw})

    columns = [
        {"key": "rank", "label": "#", "kind": "int"},
        {"key": "name", "label": "Mahsulot", "kind": "text"},
        {"key": "sku", "label": "SKU", "kind": "text"},
        {"key": "category", "label": "Kategoriya", "kind": "text"},
        {"key": "supplier", "label": "Yetkazib beruvchi", "kind": "text"},
        {"key": "quantity", "label": "Sotilgan", "kind": "int"},
        {"key": "revenue", "label": "Daromad", "kind": "money"},
        {"key": "profit", "label": "Sof foyda", "kind": "money"},
    ]
    rows = [
        {
            "rank": i + 1,
            "name": r["product__name"] or "-",
            "sku": r["product__sku"] or "-",
            "category": r["product__category__name"] or "-",
            "supplier": smap.get(r["product_id"]) or "-",
            "quantity": r["sold_qty"],
            "revenue": _money(r["revenue_sum"]),
            "profit": _money(r["profit_sum"]),
        }
        for i, r in enumerate(rows_raw)
    ]
    total_revenue = sum((Decimal(r["revenue"]) for r in rows), Decimal("0"))
    total_profit = sum((Decimal(r["profit"]) for r in rows), Decimal("0"))
    margin = (total_profit / total_revenue * 100) if total_revenue else Decimal("0")
    summary = [
        {"label": "Mahsulotlar", "value": len(rows), "kind": "int"},
        {"label": "Jami sotilgan", "value": sum(r["quantity"] for r in rows), "kind": "int"},
        {"label": "Jami daromad", "value": _money(total_revenue), "kind": "money"},
        {"label": "Sof foyda", "value": _money(total_profit), "kind": "money"},
        {"label": "Marja", "value": f"{margin:.1f}%", "kind": "text"},
    ]
    return columns, rows, None, summary


def _build_products(params, store_id, forced_stock_status=None):
    qs = Product.objects.filter(status=Product.ProductStatus.ACTIVE).select_related("category")
    category_id = params.get("category_id")
    if category_id and str(category_id).isdigit():
        qs = qs.filter(category_id=int(category_id))
    qs = apply_token_search(qs, params.get("search"))
    qs = annotate_stock_qty(qs, store_id if store_id else None)
    stock_status = forced_stock_status or params.get("stock_status")
    qs = apply_stock_status(qs, stock_status)
    qs = qs.order_by("-stock_qty", "name")

    agg = qs.aggregate(
        n=Count("id"),
        stock=Coalesce(Sum("stock_qty"), Value(0), output_field=DecimalField()),
    )
    columns = [
        {"key": "name", "label": "Mahsulot", "kind": "text"},
        {"key": "sku", "label": "SKU", "kind": "text"},
        {"key": "barcode", "label": "Shtrix kod", "kind": "text"},
        {"key": "category", "label": "Kategoriya", "kind": "text"},
        {"key": "stock", "label": "Qoldiq", "kind": "int"},
        {"key": "min_stock", "label": "Min. qoldiq", "kind": "int"},
        {"key": "state", "label": "Holat", "kind": "text"},
    ]

    def state_label(stock):
        if stock <= 0:
            return "Tugagan"
        if stock <= LOW_STOCK_THRESHOLD:
            return "Kam qolgan"
        return "Yetarli"

    def row(p):
        stock = p.stock_qty or 0
        return {
            "name": p.name,
            "sku": p.sku or "-",
            "barcode": p.barcode or "-",
            "category": p.category.name if p.category else "-",
            "stock": stock,
            "min_stock": p.min_stock or 0,
            "state": state_label(stock),
        }

    summary = [
        {"label": "Mahsulotlar soni", "value": agg["n"], "kind": "int"},
        {"label": "Jami qoldiq (dona)", "value": agg["stock"], "kind": "int"},
    ]
    return columns, qs, row, summary


def _build_low_stock(params, store_id):
    # Kam qolgan + tugagan mahsulotlar — inventar hisobotining maxsus ko'rinishi
    columns, qs, row, _ = _build_products(params, store_id)
    qs = qs.filter(stock_qty__lte=LOW_STOCK_THRESHOLD)
    agg = qs.aggregate(
        n=Count("id"),
        out=Count("id", filter=Q(stock_qty__lte=0)),
    )
    summary = [
        {"label": "Kam qolgan/tugagan", "value": agg["n"], "kind": "int"},
        {"label": "Butunlay tugagan", "value": agg["out"], "kind": "int"},
    ]
    return columns, qs, row, summary


def _build_customers(params, store_id):
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)
    period_sales = Q(
        sales__created_at__gte=start, sales__created_at__lt=end,
    )
    if store_id:
        period_sales &= Q(sales__store_id=store_id)

    qs = Customer.objects.annotate(
        period_purchases=Coalesce(
            Sum("sales__total_amount", filter=period_sales, distinct=False),
            Value(Decimal("0")), output_field=DecimalField(),
        ),
    )
    search = (params.get("search") or "").strip()
    if search:
        qs = qs.filter(Q(full_name__icontains=search) | Q(phone_number__icontains=search))

    # Qarz — ledger bo'yicha (alohida so'rov, join portlashining oldini oladi)
    debt_rows = (
        CustomerDebt.objects.values("customer_id").annotate(
            debt=Coalesce(
                Sum(Case(
                    When(type="i", then=F("amount")),
                    When(type="d", then=-F("amount")),
                    default=Value(0), output_field=DecimalField(),
                )),
                Value(Decimal("0")), output_field=DecimalField(),
            )
        )
    )
    debt_map = {r["customer_id"]: r["debt"] for r in debt_rows}

    if params.get("has_debt") == "1":
        with_debt_ids = [cid for cid, d in debt_map.items() if d > 0]
        qs = qs.filter(id__in=with_debt_ids)
    qs = qs.order_by("-period_purchases", "full_name")

    columns = [
        {"key": "name", "label": "Mijoz", "kind": "text"},
        {"key": "phone", "label": "Telefon", "kind": "text"},
        {"key": "purchases", "label": "Davrdagi xaridlar", "kind": "money"},
        {"key": "debt", "label": "Qarz (jami)", "kind": "money"},
    ]

    def row(c):
        return {
            "name": c.full_name,
            "phone": c.phone_number,
            "purchases": _money(c.period_purchases),
            "debt": _money(debt_map.get(c.id, Decimal("0"))),
        }

    total_debt = sum((d for d in debt_map.values() if d > 0), Decimal("0"))
    agg = qs.aggregate(
        n=Count("id"),
        purchases=Coalesce(Sum("period_purchases"), Value(Decimal("0")), output_field=DecimalField()),
    )
    summary = [
        {"label": "Mijozlar soni", "value": agg["n"], "kind": "int"},
        {"label": "Davrdagi xaridlar", "value": _money(agg["purchases"]), "kind": "money"},
        {"label": "Jami qarzdorlik", "value": _money(total_debt), "kind": "money"},
    ]
    return columns, qs, row, summary


def _build_suppliers(params, store_id):
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)
    period = Q(transactions__created_at__gte=start, transactions__created_at__lt=end)
    if store_id:
        period &= Q(transactions__entry__store_id=store_id)

    qs = Supplier.objects.filter(is_active=True).annotate(
        period_in=Coalesce(
            Sum("transactions__amount", filter=period & Q(transactions__type="in")),
            Value(Decimal("0")), output_field=DecimalField(),
        ),
        period_paid=Coalesce(
            Sum("transactions__amount", filter=period & Q(transactions__type="pay")),
            Value(Decimal("0")), output_field=DecimalField(),
        ),
        total_in=Coalesce(
            Sum("transactions__amount", filter=Q(transactions__type="in")),
            Value(Decimal("0")), output_field=DecimalField(),
        ),
        total_paid=Coalesce(
            Sum("transactions__amount", filter=Q(transactions__type="pay")),
            Value(Decimal("0")), output_field=DecimalField(),
        ),
    )
    search = (params.get("search") or "").strip()
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(phone_number__icontains=search))
    qs = qs.order_by("-period_in", "name")

    columns = [
        {"key": "name", "label": "Ta'minotchi", "kind": "text"},
        {"key": "phone", "label": "Telefon", "kind": "text"},
        {"key": "period_in", "label": "Davrdagi kirim (qarzga)", "kind": "money"},
        {"key": "period_paid", "label": "Davrdagi to'lovlar", "kind": "money"},
        {"key": "debt", "label": "Qarz (jami)", "kind": "money"},
    ]

    def row(s):
        return {
            "name": s.name,
            "phone": s.phone_number or "-",
            "period_in": _money(s.period_in),
            "period_paid": _money(s.period_paid),
            "debt": _money((s.total_in or 0) - (s.total_paid or 0)),
        }

    agg = qs.aggregate(
        n=Count("id"),
        p_in=Coalesce(Sum("period_in"), Value(Decimal("0")), output_field=DecimalField()),
        p_paid=Coalesce(Sum("period_paid"), Value(Decimal("0")), output_field=DecimalField()),
    )
    summary = [
        {"label": "Ta'minotchilar", "value": agg["n"], "kind": "int"},
        {"label": "Davrdagi kirim", "value": _money(agg["p_in"]), "kind": "money"},
        {"label": "Davrdagi to'lovlar", "value": _money(agg["p_paid"]), "kind": "money"},
    ]
    return columns, qs, row, summary


def _build_payments(params, store_id):
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)
    qs = (
        Payment.objects
        .filter(created_at__gte=start, created_at__lt=end)
        .filter(_store_q(store_id, "sale__store_id"))
        .select_related("bank_card", "sale")
    )
    ptype = params.get("payment_method")
    if ptype in ("cash", "card"):
        qs = qs.filter(type=ptype)
    bank_card = params.get("bank_card_id")
    if bank_card and str(bank_card).isdigit():
        qs = qs.filter(bank_card_id=int(bank_card))
    qs = qs.order_by("-created_at")

    agg = qs.aggregate(
        n=Count("id"),
        net=Coalesce(
            Sum(Case(
                When(is_refund=True, then=-F("amount")),
                default=F("amount"), output_field=DecimalField(),
            )),
            Value(Decimal("0")), output_field=DecimalField(),
        ),
    )
    columns = [
        {"key": "date", "label": "Sana", "kind": "text"},
        {"key": "sale", "label": "Chek №", "kind": "int"},
        {"key": "method", "label": "Usul", "kind": "text"},
        {"key": "kind", "label": "Turi", "kind": "text"},
        {"key": "amount", "label": "Summa", "kind": "money"},
    ]

    def row(p):
        if p.is_refund:
            kind = "Qaytarim"
        elif p.is_debt_payment:
            kind = "Qarz to'lovi"
        else:
            kind = "Sotuv"
        return {
            "date": timezone.localtime(p.created_at).strftime("%d.%m.%Y %H:%M"),
            "sale": p.sale_id or "-",
            "method": "Naqd" if p.type == "cash" else (p.bank_card.name if p.bank_card else "Karta"),
            "kind": kind,
            "amount": _money(-p.amount if p.is_refund else p.amount),
        }

    summary = [
        {"label": "To'lovlar soni", "value": agg["n"], "kind": "int"},
        {"label": "Sof tushum (NET)", "value": _money(agg["net"]), "kind": "money"},
    ]
    return columns, qs, row, summary


def _build_expenses(params, store_id):
    d_from, d_to = _parse_dates(params)
    rows_raw = ExpensesService.get(d_from, d_to, store_id)
    columns = [
        {"key": "method", "label": "Chiqim turi", "kind": "text"},
        {"key": "count", "label": "Soni", "kind": "int"},
        {"key": "amount", "label": "Summa", "kind": "money"},
        {"key": "percent", "label": "Ulushi", "kind": "text"},
    ]
    rows = [
        {"method": r["method"], "count": r["count"], "amount": _money(r["amount"]), "percent": r["percent"]}
        for r in rows_raw
    ]
    total = sum(Decimal(r["amount"]) for r in rows)
    summary = [
        {"label": "Chiqim turlari", "value": len(rows), "kind": "int"},
        {"label": "Jami chiqim", "value": _money(total), "kind": "money"},
    ]
    return columns, rows, None, summary


def _last_supplier_map(product_ids, before=None) -> dict:
    """
    product_id → oxirgi kirim (StockEntry) ta'minotchisi nomi.
    Mahsulotda to'g'ridan-to'g'ri supplier maydoni yo'q — bog'lanish kirim
    tarixidan olinadi: id bo'yicha o'sish tartibida yozib borilganda oxirgi
    kirim ta'minotchisi dictda qoladi.

    before — o'tmish holati (as-of) hisoboti uchun: shu vaqtdan keyingi
    kirimlar hisobga olinmaydi, ya'ni o'sha sanadagi ta'minotchi ko'rinadi.
    """
    smap = {}
    items = (
        StockEntryItem.objects
        .filter(product_id__in=product_ids)
        .order_by("id")
        .values("product_id", "entry__supplier__name")
    )
    if before is not None:
        items = items.filter(entry__created_at__lt=before)
    for it in items:
        smap[it["product_id"]] = it["entry__supplier__name"]
    return smap


def _supplied_by_filter(qs, params, product_field="product_id"):
    """supplier_id param berilsa — shu ta'minotchidan kirim qilingan mahsulotlargina."""
    supplier_id = params.get("supplier_id")
    if supplier_id and str(supplier_id).isdigit():
        qs = qs.filter(Exists(
            StockEntryItem.objects.filter(
                product_id=OuterRef(product_field),
                entry__supplier_id=int(supplier_id),
            )
        ))
    return qs


def _build_supplier_sales(params, store_id):
    """
    BILLZ "Yetkazib beruvchilar bo'yicha sotuvlar" formati: do'kon + sana +
    mahsulot kesimida sotilgan/qaytarilgan soni va tushum, mahsulotning
    (oxirgi kirimdagi) ta'minotchisi bilan.
    """
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)

    qs = (
        SaleItem.objects
        .filter(sale__created_at__gte=start, sale__created_at__lt=end)
        .filter(_store_q(store_id, "sale__store_id"))
        .exclude(sale__status=Sale.Status.RETURNED)
    )
    category_id = params.get("category_id")
    if category_id and str(category_id).isdigit():
        qs = qs.filter(product__category_id=int(category_id))
    qs = _supplied_by_filter(qs, params)

    search = (params.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(product__name__icontains=search)
            | Q(product__sku__icontains=search)
            | Q(product__barcode__icontains=search)
        )

    # Tafsilot: kunlar bo'yicha (standart) yoki davr jami
    by_day = params.get("group_mode") != "period"
    group_fields = [
        "sale__store__name", "product_id", "product__name",
        "product__sku", "product__barcode", "product__category__name",
    ]
    if by_day:
        qs = qs.annotate(day=TruncDate("sale__created_at"))
        group_fields = ["day"] + group_fields

    grouped = (
        qs.values(*group_fields)
        .annotate(
            sold_qty=Coalesce(Sum("quantity"), Value(0), output_field=DecimalField()),
            returned_qty=Coalesce(Sum("returned_quantity"), Value(0), output_field=DecimalField()),
            revenue_sum=Coalesce(
                Sum(ExpressionWrapper(
                    F("unit_price") * (F("quantity") - F("returned_quantity")),
                    output_field=DecimalField(),
                )),
                Value(Decimal("0")), output_field=DecimalField(),
            ),
        )
        .order_by(*((["-day"] if by_day else []) + ["sale__store__name", "product__name"]))
    )
    rows_raw = list(grouped[:LARGE_EXPORT_CAP])
    smap = _last_supplier_map({r["product_id"] for r in rows_raw})

    period_label = f"{d_from.isoformat()} — {d_to.isoformat()}"
    columns = [
        {"key": "store", "label": "Do'kon", "kind": "text"},
        {"key": "date", "label": "Sana", "kind": "text"},
        {"key": "supplier", "label": "Yetkazib beruvchi", "kind": "text"},
        {"key": "name", "label": "Nomi", "kind": "text"},
        {"key": "sku", "label": "Artikul", "kind": "text"},
        {"key": "barcode", "label": "Shtrix-kod", "kind": "text"},
        {"key": "category", "label": "Toifa", "kind": "text"},
        {"key": "sold", "label": "Sotilganlar soni", "kind": "int"},
        {"key": "returned", "label": "Qaytarilganlar soni", "kind": "int"},
        {"key": "net", "label": "Sof sotilgan", "kind": "int"},
        {"key": "revenue", "label": "Tushum", "kind": "money"},
    ]
    rows = [
        {
            "store": r["sale__store__name"] or "-",
            "date": r["day"].isoformat() if by_day else period_label,
            "supplier": smap.get(r["product_id"]) or "-",
            "name": r["product__name"] or "-",
            "sku": r["product__sku"] or "-",
            "barcode": r["product__barcode"] or "-",
            "category": r["product__category__name"] or "-",
            "sold": r["sold_qty"],
            "returned": r["returned_qty"],
            "net": r["sold_qty"] - r["returned_qty"],
            "revenue": _money(r["revenue_sum"]),
        }
        for r in rows_raw
    ]
    summary = [
        {"label": "Qatorlar", "value": len(rows), "kind": "int"},
        {"label": "Jami sotilgan (sof)", "value": sum(r["net"] for r in rows), "kind": "int"},
        {"label": "Jami qaytarilgan", "value": sum(r["returned"] for r in rows), "kind": "int"},
        {"label": "Jami tushum", "value": _money(sum(Decimal(r["revenue"]) for r in rows)), "kind": "money"},
    ]
    return columns, rows, None, summary


def _build_stock_leftovers(params, store_id):
    """
    BILLZ "Qoldiqlar bo'yicha hisobot" formati: har (do'kon, mahsulot) uchun
    qoldiq — o'lchov birligi, toifa, brend, ta'minotchi va narxlar bilan.

    `as_of` (YYYY-MM-DD) berilsa — qoldiq SHU KUN OXIRIGA hisoblanadi, ya'ni
    "boshidan tanlangan sanagacha" holat: joriy qoldiqdan o'sha kundan keyingi
    barcha ombor harakatlari teskari qilinadi (stock_history_service). Berilmasa
    — joriy holat (avvalgi xatti-harakat, to'liq queryset yo'li bilan).
    """
    as_of = _parse_as_of(params)

    qs = (
        ProductBatch.objects
        .filter(is_active=True, product__status=Product.ProductStatus.ACTIVE)
        .select_related(
            "store", "product", "product__category",
            "product__brand", "product__unit_measurement",
        )
    )
    if store_id:
        qs = qs.filter(store_id=store_id)
    category_id = params.get("category_id")
    if category_id and str(category_id).isdigit():
        qs = qs.filter(product__category_id=int(category_id))
    qs = _supplied_by_filter(qs, params)

    state = params.get("leftover_state")
    # as_of rejimida qoldiq Python tomonda qayta hisoblanadi — holat filtri ham
    # o'sha sanadagi qiymatga qo'llanishi kerak (joriy qiymatga emas)
    if as_of is None:
        if state == "in_stock":
            qs = qs.filter(quantity__gt=0)
        elif state == "out":
            qs = qs.filter(quantity__lte=0)

    search = (params.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(product__name__icontains=search)
            | Q(product__sku__icontains=search)
            | Q(product__barcode__icontains=search)
        )
    qs = qs.order_by("store__name", "-quantity", "product__name")

    columns = [
        {"key": "store", "label": "Do'kon", "kind": "text"},
        {"key": "name", "label": "Nomi", "kind": "text"},
        {"key": "sku", "label": "Artikul", "kind": "text"},
        {"key": "barcode", "label": "Shtrix-kod", "kind": "text"},
        {"key": "unit", "label": "O'lchov birligi", "kind": "text"},
        {"key": "category", "label": "Toifa", "kind": "text"},
        {"key": "brand", "label": "Brend", "kind": "text"},
        {"key": "supplier", "label": "Yetkazib beruvchi", "kind": "text"},
        {"key": "purchase_price", "label": "Kelish narxi", "kind": "money"},
        {"key": "selling_price", "label": "Sotish narxi", "kind": "money"},
        {"key": "qty", "label": "Qoldiq", "kind": "int"},
        {"key": "value", "label": "Qoldiq summasi", "kind": "money"},
    ]

    def build_row(b, qty, smap):
        p = b.product
        return {
            "store": b.store.name if b.store else "-",
            "name": p.name,
            "sku": p.sku or "-",
            "barcode": p.barcode or "-",
            "unit": p.unit_measurement.measurement if p.unit_measurement else "-",
            "category": p.category.name if p.category else "-",
            "brand": p.brand.name if p.brand else "-",
            "supplier": smap.get(b.product_id) or "-",
            "purchase_price": _money(b.purchase_price),
            "selling_price": _money(b.selling_price),
            "qty": qty,
            "value": _money(qty * (b.purchase_price or 0)),
        }

    # ── Joriy holat: queryset + row_fn (DB tomonda saralash/aggregat/pagination)
    if as_of is None:
        agg = qs.aggregate(
            n=Count("id"),
            qty=Coalesce(Sum("quantity"), Value(0), output_field=DecimalField()),
            value=Coalesce(
                Sum(ExpressionWrapper(
                    F("quantity") * F("purchase_price"), output_field=DecimalField(),
                )),
                Value(Decimal("0")), output_field=DecimalField(),
            ),
        )
        # Ta'minotchi — bitta so'rovda barcha (filtrlangan) mahsulotlar uchun
        smap = _last_supplier_map(qs.values("product_id"))
        summary = [
            {"label": "Qatorlar (do'kon×mahsulot)", "value": agg["n"], "kind": "int"},
            {"label": "Jami qoldiq (dona)", "value": agg["qty"], "kind": "int"},
            {"label": "Qoldiq summasi (kelish narxida)", "value": _money(agg["value"]), "kind": "money"},
        ]
        return columns, qs, lambda b: build_row(b, b.quantity or 0, smap), summary

    # ── O'tmish holati: sanadan keyingi harakatlar teskari qilinadi
    cutoff = day_end(as_of)
    delta = stock_delta_after(cutoff, store_id)
    batches = list(qs[:LARGE_EXPORT_CAP])
    smap = _last_supplier_map({b.product_id for b in batches}, before=cutoff)

    rows = []
    for b in batches:
        qty = (b.quantity or 0) - delta.get((b.store_id, b.product_id), 0)
        if state == "in_stock" and qty <= 0:
            continue
        if state == "out" and qty > 0:
            continue
        rows.append(build_row(b, qty, smap))
    # Joriy holatdagi tartib bilan bir xil: do'kon → qoldiq (kamayish) → nom
    rows.sort(key=lambda r: (r["store"], -r["qty"], r["name"]))

    total_qty = sum(r["qty"] for r in rows)
    total_value = sum(Decimal(r["value"]) for r in rows)
    summary = [
        {"label": "Qatorlar (do'kon×mahsulot)", "value": len(rows), "kind": "int"},
        {"label": "Jami qoldiq (dona)", "value": total_qty, "kind": "int"},
        {"label": "Qoldiq summasi (kelish narxida)", "value": _money(total_value), "kind": "money"},
    ]
    return columns, rows, None, summary


def _parse_optional_dates(params) -> tuple[date | None, date | None]:
    """
    from/to — berilmasa BUTUN tarix. (_parse_dates oxirgi 30 kunni beradi;
    mahsulot tarixida bu noto'g'ri — mahsulot bir yil oldin kirgan bo'lishi
    mumkin va hisobot bo'sh chiqardi.)
    """
    parsed = []
    for key in ("from", "to"):
        raw = (params.get(key) or "").strip()
        if not raw:
            parsed.append(None)
            continue
        try:
            parsed.append(date.fromisoformat(raw))
        except ValueError:
            raise ValidationError({key: "ISO format: YYYY-MM-DD"})
    return parsed[0], parsed[1]


def _event_status_label(event: dict) -> str:
    status = event.get("status")
    if not status:
        return "-"
    return PRODUCT_EVENT_STATUS_LABELS.get(event["type"], {}).get(status, str(status))


def _dt_label(value) -> str:
    return timezone.localtime(value).strftime("%d.%m.%Y") if value else "-"


def _build_product_history(params, store_id, user):
    """
    Bitta mahsulotning kartochkasi (info) + harakatlar tarixi (kirim, o'tkazma,
    sotuv, qaytimlar, spisaniye, inventarizatsiya) bitta jadvalda.

    Hisob-kitob ProductHistoryService'da — mahsulot tarixi sahifasi bilan
    AYNAN bir xil manba, shuning uchun raqamlar hech qachon farq qilmaydi.
    Do'kon ruxsati ham o'sha servisda (xodim faqat o'z do'konlari yozuvlarini
    ko'radi), shuning uchun bu yerga `user` uzatiladi.
    """
    raw_id = str(params.get("product_id") or "").strip()
    if not raw_id.isdigit():
        raise ValidationError({"product_id": "Mahsulotni tanlang"})

    product = (
        Product.objects
        .select_related("category", "brand", "unit_measurement")
        .filter(pk=int(raw_id))
        .first()
    )
    if product is None:
        raise ValidationError({"product_id": "Mahsulot topilmadi"})

    d_from, d_to = _parse_optional_dates(params)
    event_type = params.get("event_type") or None
    if event_type not in PRODUCT_EVENT_LABELS:
        event_type = None

    service = ProductMovementReportService(
        product,
        user,
        date_from=parse_date_param(d_from.isoformat() if d_from else None),
        date_to=parse_date_param(d_to.isoformat() if d_to else None, end_of_day=True),
        store_id=store_id,
    )
    by_store = service.build_by_store()
    totals = service.build_summary(by_store)
    events = service.collect_events(limit=PRODUCT_HISTORY_MAX_EVENTS, event_type=event_type)
    total_events = service.count_events(event_type)

    columns = [
        {"key": "date", "label": "Sana", "kind": "text"},
        {"key": "event", "label": "Harakat", "kind": "text"},
        {"key": "doc_id", "label": "Hujjat №", "kind": "int"},
        {"key": "store", "label": "Do'kon", "kind": "text"},
        {"key": "to_store", "label": "Qabul qiluvchi", "kind": "text"},
        {"key": "quantity", "label": "Miqdor", "kind": "int"},
        {"key": "price", "label": "Narx", "kind": "money"},
        {"key": "amount", "label": "Summa", "kind": "money"},
        {"key": "counterparty", "label": "Kontragent", "kind": "text"},
        {"key": "user", "label": "Xodim", "kind": "text"},
        {"key": "status", "label": "Holat", "kind": "text"},
        {"key": "note", "label": "Izoh", "kind": "text"},
    ]

    rows = [
        {
            "date": timezone.localtime(e["date"]).strftime("%d.%m.%Y %H:%M"),
            "event": PRODUCT_EVENT_LABELS.get(e["type"], e["type"]),
            "doc_id": e["doc_id"],
            "store": e.get("store_name") or "-",
            "to_store": e.get("to_store_name") or "-",
            "quantity": e["quantity"],
            "price": _money(e["price"]),
            "amount": _money(e["amount"]),
            "counterparty": e.get("counterparty") or "-",
            "user": e.get("user") or "-",
            "status": _event_status_label(e),
            "note": e.get("note") or "-",
        }
        for e in events
    ]

    summary = [
        {"label": "Kirim (dona)", "value": totals["purchased_qty"], "kind": "int"},
        {"label": "Sotilgan (dona)", "value": totals["sold_qty"], "kind": "int"},
        {"label": "Sotuv summasi", "value": _money(totals["sold_amount"]), "kind": "money"},
        {"label": "Foyda", "value": _money(totals["profit"]), "kind": "money"},
        {"label": "Sotuv qaytimi (dona)", "value": totals["sale_returned_qty"], "kind": "int"},
        {"label": "Spisaniye (dona)", "value": totals["written_off_qty"], "kind": "int"},
        {"label": "O'tkazilgan (dona)", "value": totals["transferred_qty"], "kind": "int"},
        {"label": "Joriy qoldiq", "value": totals["current_qty"], "kind": "int"},
    ]

    identity = " · ".join(
        part for part in (
            f"SKU: {product.sku}" if product.sku else "",
            f"Shtrix: {product.barcode}" if product.barcode else "",
        ) if part
    )
    fields = [
        {"label": "Kategoriya", "value": product.category.name if product.category_id else "-"},
        {"label": "Brend", "value": product.brand.name if product.brand_id else "-"},
        {
            "label": "O'lchov birligi",
            "value": product.unit_measurement.measurement if product.unit_measurement_id else "-",
        },
        {"label": "Holat", "value": product.get_status_display()},
        {"label": "Joriy qoldiq", "value": totals["current_qty"], "kind": "int"},
        {"label": "Minimal qoldiq", "value": product.min_stock, "kind": "int"},
        {"label": "O'rtacha kirim narxi", "value": _money(totals["avg_purchase_price"]), "kind": "money"},
        {"label": "O'rtacha sotuv narxi", "value": _money(totals["avg_selling_price"]), "kind": "money"},
        {"label": "Kirimlar soni", "value": totals["entry_count"], "kind": "int"},
        {"label": "Ta'minotchilar", "value": totals["supplier_count"], "kind": "int"},
        {"label": "Birinchi kirim", "value": _dt_label(totals["first_entry_at"])},
        {"label": "Oxirgi kirim", "value": _dt_label(totals["last_entry_at"])},
        {"label": "Oxirgi sotuv", "value": _dt_label(totals["last_sale_at"])},
        {"label": "Harakatlar soni", "value": total_events, "kind": "int"},
    ]
    # Tannarxi yo'q sotuvlar bo'lsa foyda to'liq emas — yashirmaymiz
    if totals["profit_partial"]:
        fields.append({
            "label": "Diqqat",
            "value": "Ba'zi sotuvlarda tannarx yo'q — foyda taxminiy",
        })
    # Cap urilgan bo'lsa jimgina kesmaymiz, aytamiz
    if total_events > len(rows):
        fields.append({
            "label": "Eslatma",
            "value": f"Jadvalda oxirgi {len(rows)} ta harakat ({total_events} tadan)",
        })

    info = {"title": product.name, "subtitle": identity, "fields": fields}
    return columns, rows, None, summary, info


# ─────────────────────────────────────────────
#  REGISTRY
# ─────────────────────────────────────────────
def _bank_card_pairs():
    return [(str(c["id"]), c["name"]) for c in BankCard.objects.filter(is_active=True).values("id", "name")]


REPORTS = {
    "sales": {
        "label": "Sotuvlar hisoboti",
        "builder": _build_sales,
        "search": True,
        "filters": lambda: [
            _f_daterange(), _f_store(),
            _f_select("payment_type", "To'lov turi", list(PAYMENT_TYPE_LABELS.items()), "Barchasi"),
            _f_select("status", "Holat", list(SALE_STATUS_LABELS.items()), "Barchasi"),
        ],
    },
    "top_products": {
        "label": "Ko'p sotilgan mahsulotlar",
        "builder": _build_top_products,
        "search": False,
        "filters": lambda: [
            _f_daterange(), _f_store(), _f_supplier(), _f_category(),
            _f_select("top", "Nechta (TOP)", [("10", "Top 10"), ("20", "Top 20"), ("50", "Top 50"), ("100", "Top 100")]),
            _f_select("sort_by", "Saralash", [
                ("revenue", "Daromad bo'yicha"),
                ("quantity", "Miqdor bo'yicha"),
                ("profit", "Sof foyda bo'yicha"),
            ]),
        ],
    },
    "products": {
        "label": "Mahsulotlar / inventar hisoboti",
        "builder": _build_products,
        "search": True,
        "filters": lambda: [
            _f_store(), _f_category(),
            _f_select("stock_status", "Qoldiq holati", [
                ("in_stock", "Yetarli"), ("low_stock", "Kam qolgan"), ("out_of_stock", "Tugagan"),
            ], "Barchasi"),
        ],
    },
    "low_stock": {
        "label": "Kam qolgan mahsulotlar",
        "builder": _build_low_stock,
        "search": True,
        "filters": lambda: [_f_store(), _f_category()],
    },
    "product_history": {
        "label": "Mahsulot tarixi (bitta mahsulot)",
        "builder": _build_product_history,
        "search": False,
        # Do'kon ruxsati ProductHistoryService ichida hisoblanadi
        "needs_user": True,
        # Lenta baribir PRODUCT_HISTORY_MAX_EVENTS bilan chegaralangan
        "export_cap": PRODUCT_HISTORY_MAX_EVENTS,
        "filters": lambda: [
            _f_product(), _f_daterange(), _f_store(),
            _f_select("event_type", "Harakat turi", list(PRODUCT_EVENT_LABELS.items()), "Barchasi"),
        ],
    },
    "customers": {
        "label": "Mijozlar hisoboti",
        "builder": _build_customers,
        "search": True,
        "filters": lambda: [
            _f_daterange(), _f_store(),
            _f_select("has_debt", "Qarzdorlik", [("1", "Faqat qarzdorlar")], "Barchasi"),
        ],
    },
    "suppliers": {
        "label": "Ta'minotchilar hisoboti",
        "builder": _build_suppliers,
        "search": True,
        "filters": lambda: [_f_daterange(), _f_store()],
    },
    "supplier_sales": {
        "label": "Yetkazib beruvchilar bo'yicha sotuvlar",
        "builder": _build_supplier_sales,
        "search": True,
        # Katalog katta — standart 5k cap kesib qo'ymasin (to'liq eksport kerak)
        "export_cap": LARGE_EXPORT_CAP,
        "filters": lambda: [
            _f_daterange(), _f_store(), _f_supplier(), _f_category(),
            _f_select("group_mode", "Tafsilotlar", [
                ("day", "Kunlar bo'yicha"), ("period", "Davr jami"),
            ]),
        ],
    },
    "stock_leftovers": {
        "label": "Qoldiqlar bo'yicha hisobot",
        "builder": _build_stock_leftovers,
        "search": True,
        # Do'kon×mahsulot qatorlari 5k dan ko'p — to'liq eksport uchun keng cap
        "export_cap": LARGE_EXPORT_CAP,
        "filters": lambda: [
            # Boshidan shu kun oxirigacha bo'lgan holat; bo'sh — bugungi holat
            _f_date("as_of", "Holat sanasi (bo'sh — bugun)"),
            _f_store(), _f_category(), _f_supplier(),
            _f_select("leftover_state", "Qoldiq holati", [
                ("in_stock", "Bor (>0)"), ("out", "Tugagan (0)"),
            ], "Barchasi"),
        ],
    },
    "payments": {
        "label": "To'lovlar hisoboti",
        "builder": _build_payments,
        "search": False,
        "filters": lambda: [
            _f_daterange(), _f_store(),
            _f_select("payment_method", "Usul", [("cash", "Naqd"), ("card", "Karta")], "Barchasi"),
            _f_select("bank_card_id", "Karta turi", _bank_card_pairs(), "Barchasi"),
        ],
    },
    "expenses": {
        "label": "Chiqimlar hisoboti",
        "builder": _build_expenses,
        "search": False,
        "filters": lambda: [_f_daterange(), _f_store()],
    },
}


class ReportBuilderService:

    @staticmethod
    def meta() -> dict:
        return {
            "reports": [
                {
                    "key": key,
                    "label": spec["label"],
                    "search": spec["search"],
                    "filters": spec["filters"](),
                }
                for key, spec in REPORTS.items()
            ]
        }

    @staticmethod
    def _run(params, user=None) -> tuple[list, list, object, list, dict | None]:
        """
        Builder ishga tushirib (columns, rows/queryset, row_fn, summary, info)
        qaytaradi. `info` — ixtiyoriy kartochka bloki (masalan mahsulot
        tafsilotlari); builder qaytarmasa None.
        """
        report_type = params.get("report_type")
        spec = REPORTS.get(report_type)
        if not spec:
            raise ValidationError({"report_type": "Noma'lum hisobot turi"})
        store_id = _parse_store(params)
        # Ayrim hisobotlar (mahsulot tarixi) do'kon ruxsatini o'zi hisoblaydi
        if spec.get("needs_user"):
            result = spec["builder"](params, store_id, user)
        else:
            result = spec["builder"](params, store_id)
        columns, rows_or_qs, row_fn, summary = result[:4]
        info = result[4] if len(result) > 4 else None
        return columns, rows_or_qs, row_fn, summary, info

    @staticmethod
    def generate(params, user=None) -> dict:
        columns, rows_or_qs, row_fn, summary, info = ReportBuilderService._run(params, user)
        page = max(1, _parse_int(params, "page", 1))
        limit = min(MAX_LIMIT, max(1, _parse_int(params, "limit", DEFAULT_LIMIT)))
        offset = (page - 1) * limit

        if row_fn is None:
            # Tayyor ro'yxat (aggregatsiyalangan kichik hisobotlar)
            total = len(rows_or_qs)
            rows = rows_or_qs[offset:offset + limit]
        else:
            total = rows_or_qs.count()
            rows = [row_fn(obj) for obj in rows_or_qs[offset:offset + limit]]

        data = {
            "columns": columns,
            "rows": rows,
            "summary": summary,
            "total": total,
            "page": page,
            "limit": limit,
        }
        if info:
            data["info"] = info
        return data

    @staticmethod
    def export_rows(params, user=None) -> tuple[str, list, list, list, dict | None]:
        """Eksport uchun: (label, columns, BARCHA qatorlar[cap], summary, info) — generate bilan bir xil filtrlar."""
        report_type = params.get("report_type")
        spec = REPORTS.get(report_type)
        if not spec:
            raise ValidationError({"report_type": "Noma'lum hisobot turi"})
        columns, rows_or_qs, row_fn, summary, info = ReportBuilderService._run(params, user)
        cap = spec.get("export_cap", EXPORT_MAX_ROWS)
        if row_fn is None:
            rows = list(rows_or_qs)[:cap]
        else:
            rows = [row_fn(obj) for obj in rows_or_qs[:cap]]
        # Fayl sarlavhasida holat sanasi ko'rinsin — o'tmish qoldig'i joriysi
        # bilan aralashib ketmasligi uchun
        label = spec["label"]
        as_of = _parse_as_of(params)
        if as_of:
            label = f"{label} ({as_of.strftime('%d.%m.%Y')} holatiga)"
        # Bitta obyekt bo'yicha hisobotda (mahsulot tarixi) sarlavhada uning
        # nomi turadi — bir nechta yuklangan fayl aralashib ketmasligi uchun
        if info and info.get("title"):
            label = f"{label} — {info['title']}"
        return label, columns, rows, summary, info
