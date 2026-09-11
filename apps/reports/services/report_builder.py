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
from apps.products.models import Brand, Category, Product, ProductBatch
from apps.products.services.product_history_service import parse_date_param
from apps.reports.services.product_movement_report_service import ProductMovementReportService
from apps.products.services.product_query_service import (
    LOW_STOCK_THRESHOLD,
    annotate_stock_qty,
    apply_stock_status,
    apply_token_search,
)
from apps.sales.models import BankCard, Payment, Sale, SaleItem, SaleReturn
from apps.sales.profit import partial_cost_filter, sum_item_profit
from apps.store.models import Store
from apps.users.models.customers import Customer
from apps.users.models.user import User

from .report_service import _dt_bounds, _store_q, ExpensesService
from .reporting_foundation import ReportingFoundationService
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


def _f_brand():
    options = [{"value": "", "label": "Barcha brendlar"}] + [
        {"value": str(b["id"]), "label": b["name"]}
        for b in Brand.objects.values("id", "name").order_by("name")
    ]
    return {"param": "brand_id", "type": "select", "label": "Brend", "options": options}


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


def _f_text(param, label):
    return {"param": param, "type": "text", "label": label}


def _f_seller():
    options = [{"value": "", "label": "Barcha xodimlar"}] + [
        {"value": str(u["id"]), "label": u["full_name"] or u["phone_number"]}
        for u in User.objects.filter(is_active=True).values("id", "full_name", "phone_number").order_by("full_name")
    ]
    return {"param": "seller_id", "type": "select", "label": "Sotuvchi", "options": options}


def _f_user(param="user_id", label="Mas'ul xodim"):
    options = [{"value": "", "label": "Barcha xodimlar"}] + [
        {"value": str(u["id"]), "label": u["full_name"] or u["phone_number"]}
        for u in User.objects.filter(is_active=True).values("id", "full_name", "phone_number").order_by("full_name")
    ]
    return {"param": param, "type": "select", "label": label, "options": options}


# ─────────────────────────────────────────────
#  BUILDERLAR — har biri (columns, rows, summary) qaytaradi
#  rows: dict ro'yxati (column key → qiymat)
# ─────────────────────────────────────────────
def _build_sales(params, store_id):
    # Sana tanlanmasa — boshidan bugungacha JAMI (foydalanuvchi "hammasi"ni
    # ko'rish uchun har safar sana tanlab o'tirmasin)
    d_from, d_to = _parse_dates(params, default_all=True)
    start, end = _dt_bounds(d_from, d_to)

    qs = (
        Sale.objects
        .filter(created_at__gte=start, created_at__lt=end)
        .filter(_store_q(store_id))
        .select_related("store", "customer", "seller")
    )
    # Reporting Foundation orqali qisman va to'liq qaytarimlarni chegiruvchi sof maydonlar
    qs = ReportingFoundationService.annotate_sale_net_fields(qs)

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

    # Davrda rasmiylashtirilgan qaytarimlar (Period return logic)
    ret_filter = Q(created_at__gte=start, created_at__lt=end)
    if store_id:
        ret_filter &= Q(store_id=store_id)
    period_refund = SaleReturn.objects.filter(ret_filter).aggregate(
        total=Coalesce(Sum("total_refund"), Value(Decimal("0")), output_field=DecimalField())
    )["total"]

    agg = qs.aggregate(
        n=Count("id"),
        active_n=Count("id", filter=~Q(status=Sale.Status.RETURNED)),
        total=Coalesce(Sum("net_total"), Value(Decimal("0")), output_field=DecimalField()),
        paid=Coalesce(Sum("net_paid"), Value(Decimal("0")), output_field=DecimalField()),
        debt=Coalesce(Sum("net_debt"), Value(Decimal("0")), output_field=DecimalField()),
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
        net_tot = getattr(s, "net_total", s.total_amount)
        net_pd = getattr(s, "net_paid", s.paid_amount)
        net_db = getattr(s, "net_debt", (s.total_amount or 0) - (s.paid_amount or 0))
        return {
            "id": s.id,
            "date": timezone.localtime(s.created_at).strftime("%d.%m.%Y %H:%M"),
            "store": s.store.name if s.store else "-",
            "customer": s.customer.full_name if s.customer else "-",
            "seller": (s.seller.full_name or "-") if s.seller else "-",
            "total": _money(net_tot),
            "paid": _money(net_pd),
            "debt": _money(net_db),
            "profit": _money(getattr(s, "net_profit", 0)),
            "payment": PAYMENT_TYPE_LABELS.get(s.payment_type, s.payment_type),
            "status": SALE_STATUS_LABELS.get(s.status, s.status),
        }

    revenue = agg["total"]
    margin = (agg["profit"] / revenue * 100) if revenue else Decimal("0")
    sales_count = agg["active_n"] if sale_status != "r" else agg["n"]
    summary = [
        {"label": "Davr", "value": _period_label(params, d_from, d_to), "kind": "text"},
        {"label": "Sotuvlar soni", "value": sales_count, "kind": "int"},
        {"label": "Jami summa", "value": _money(agg["total"]), "kind": "money"},
        {"label": "To'langan", "value": _money(agg["paid"]), "kind": "money"},
        {"label": "Qarz", "value": _money(agg["debt"]), "kind": "money"},
        {"label": "Sof foyda", "value": _money(agg["profit"]), "kind": "money"},
        {"label": "Marja", "value": f"{margin:.1f}%", "kind": "text"},
    ]
    if period_refund > Decimal("0"):
        summary.append({
            "label": "Davrdagi qaytarimlar",
            "value": _money(period_refund),
            "kind": "money",
        })
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


def _build_sales_by_product(params, store_id):
    """
    BILLZ "Tovarlar bo'yicha sotuvlar" formati:
    Har bir mahsulot bo'yicha tanlangan davrda qancha sotilgan, qancha qaytarilgan,
    qancha sof sotilgan, chegirmagacha savdo, chegirma, sof tushum, tannarx,
    sof foyda va marja foizi.
    """
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)
    period_label = f"{d_from.strftime('%d.%m.%Y')} — {d_to.strftime('%d.%m.%Y')}"

    category_id = int(params["category_id"]) if (params.get("category_id") or "").isdigit() else None
    brand_id = int(params["brand_id"]) if (params.get("brand_id") or "").isdigit() else None
    product_id = int(params["product_id"]) if (params.get("product_id") or "").isdigit() else None
    seller_id = int(params["seller_id"]) if (params.get("seller_id") or "").isdigit() else None
    customer_id = int(params["customer_id"]) if (params.get("customer_id") or "").isdigit() else None
    sku = params.get("sku")
    barcode = params.get("barcode")
    search = params.get("search")
    sort_by = params.get("sort_by") if params.get("sort_by") in ("quantity", "profit", "revenue") else "revenue"

    raw_rows, totals = ReportingFoundationService.get_sales_by_product_metrics(
        start=start,
        end=end,
        store_id=store_id,
        category_id=category_id,
        brand_id=brand_id,
        product_id=product_id,
        sku=sku,
        barcode=barcode,
        seller_id=seller_id,
        customer_id=customer_id,
        search=search,
        sort_by=sort_by,
    )

    columns = [
        {"key": "store", "label": "Do'kon", "kind": "text"},
        {"key": "date", "label": "Davr", "kind": "text"},
        {"key": "name", "label": "Tovar", "kind": "text"},
        {"key": "sku", "label": "SKU", "kind": "text"},
        {"key": "barcode", "label": "Shtrix kod", "kind": "text"},
        {"key": "category", "label": "Kategoriya", "kind": "text"},
        {"key": "brand", "label": "Brend", "kind": "text"},
        {"key": "unit", "label": "O'lchov birligi", "kind": "text"},
        {"key": "sold_qty", "label": "Sotilgan", "kind": "int"},
        {"key": "returned_qty", "label": "Qaytarilgan", "kind": "int"},
        {"key": "net_sold_qty", "label": "Sof sotilgan", "kind": "int"},
        {"key": "gross_sales", "label": "Chegirmagacha savdo", "kind": "money"},
        {"key": "discount", "label": "Chegirma", "kind": "money"},
        {"key": "net_revenue", "label": "Sof tushum", "kind": "money"},
        {"key": "free_price", "label": "Erkin narx", "kind": "text"},
        {"key": "unit_cost", "label": "Birlik tannarxi", "kind": "money"},
        {"key": "total_cost", "label": "Tannarx", "kind": "money"},
        {"key": "profit", "label": "Foyda", "kind": "money"},
        {"key": "margin_pct", "label": "Margin %", "kind": "text"},
    ]

    formatted_rows = [
        {
            "store": r["store"],
            "date": period_label,
            "name": r["name"],
            "sku": r["sku"],
            "barcode": r["barcode"],
            "category": r["category"],
            "brand": r["brand"],
            "unit": r["unit"],
            "sold_qty": r["sold_qty"],
            "returned_qty": r["returned_qty"],
            "net_sold_qty": r["net_sold_qty"],
            "gross_sales": _money(r["gross_sales"]),
            "discount": _money(r["discount"]),
            "net_revenue": _money(r["net_revenue"]),
            "free_price": r["free_price"],
            "unit_cost": _money(r["unit_cost"]),
            "total_cost": _money(r["total_cost"]),
            "profit": _money(r["profit"]),
            "margin_pct": f"{r['margin_pct']:.1f}%",
        }
        for r in raw_rows
    ]

    summary = [
        {"label": "Davr", "value": period_label, "kind": "text"},
        {"label": "Tovarlar soni", "value": totals["count"], "kind": "int"},
        {"label": "Jami sotilgan", "value": totals["total_sold_qty"], "kind": "int"},
        {"label": "Jami qaytarilgan", "value": totals["total_ret_qty"], "kind": "int"},
        {"label": "Sof sotilgan", "value": totals["total_net_sold_qty"], "kind": "int"},
        {"label": "Chegirmagacha savdo", "value": _money(totals["total_gross_sales"]), "kind": "money"},
        {"label": "Jami chegirma", "value": _money(totals["total_discount"]), "kind": "money"},
        {"label": "Sof tushum", "value": _money(totals["total_net_revenue"]), "kind": "money"},
        {"label": "Jami tannarx", "value": _money(totals["total_cost"]), "kind": "money"},
        {"label": "Sof foyda", "value": _money(totals["total_profit"]), "kind": "money"},
        {"label": "O'rtacha marja", "value": f"{totals['overall_margin']:.1f}%", "kind": "text"},
    ]

    if totals["any_missing_cost"]:
        summary.append({
            "label": "Diqqat",
            "value": "Ba'zi tovarlarda tannarx yo'q — foyda taxminiy",
            "kind": "text",
        })

    return columns, formatted_rows, None, summary


def _build_product_efficiency(params, store_id):
    """
    BILLZ "Tovarlar samaradorligi" formati:
    Sotuvlar tezligi (Sales velocity), qoldiq kunlari (Days of Inventory - DOI),
    tushum ulushi (Revenue share %) va samaradorlik holatlari (Optimal, Tez sotiluvchi,
    Ortiqcha zaxira, Harakatsiz zaxira, Tugagan).
    """
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)
    period_label = f"{d_from.strftime('%d.%m.%Y')} — {d_to.strftime('%d.%m.%Y')}"

    category_id = int(params["category_id"]) if (params.get("category_id") or "").isdigit() else None
    brand_id = int(params["brand_id"]) if (params.get("brand_id") or "").isdigit() else None
    product_id = int(params["product_id"]) if (params.get("product_id") or "").isdigit() else None
    seller_id = int(params["seller_id"]) if (params.get("seller_id") or "").isdigit() else None
    customer_id = int(params["customer_id"]) if (params.get("customer_id") or "").isdigit() else None
    sku = params.get("sku")
    barcode = params.get("barcode")
    search = params.get("search")
    efficiency_status = params.get("efficiency_status")
    allowed_sorts = {"revenue", "quantity", "profit", "velocity", "stock", "doi"}
    sort_by = params.get("sort_by") if params.get("sort_by") in allowed_sorts else "revenue"

    raw_rows, totals = ReportingFoundationService.get_product_efficiency_metrics(
        start=start,
        end=end,
        store_id=store_id,
        category_id=category_id,
        brand_id=brand_id,
        product_id=product_id,
        sku=sku,
        barcode=barcode,
        seller_id=seller_id,
        customer_id=customer_id,
        efficiency_status=efficiency_status,
        search=search,
        sort_by=sort_by,
    )

    columns = [
        {"key": "store", "label": "Do'kon", "kind": "text"},
        {"key": "date", "label": "Davr", "kind": "text"},
        {"key": "name", "label": "Tovar", "kind": "text"},
        {"key": "sku", "label": "SKU", "kind": "text"},
        {"key": "barcode", "label": "Shtrix kod", "kind": "text"},
        {"key": "category", "label": "Kategoriya", "kind": "text"},
        {"key": "brand", "label": "Brend", "kind": "text"},
        {"key": "unit", "label": "O'lchov birligi", "kind": "text"},
        {"key": "current_stock", "label": "Joriy qoldiq", "kind": "int"},
        {"key": "sold_qty", "label": "Sotilgan", "kind": "int"},
        {"key": "returned_qty", "label": "Qaytarilgan", "kind": "int"},
        {"key": "net_sold_qty", "label": "Sof sotilgan", "kind": "int"},
        {"key": "gross_sales", "label": "Chegirmagacha savdo", "kind": "money"},
        {"key": "discount", "label": "Chegirma", "kind": "money"},
        {"key": "net_revenue", "label": "Sof tushum", "kind": "money"},
        {"key": "revenue_share_pct", "label": "Tushum ulushi", "kind": "text"},
        {"key": "unit_cost", "label": "Birlik tannarxi", "kind": "money"},
        {"key": "total_cost", "label": "Tannarx", "kind": "money"},
        {"key": "profit", "label": "Foyda", "kind": "money"},
        {"key": "margin_pct", "label": "Margin %", "kind": "text"},
        {"key": "sales_velocity", "label": "Kunlik sotuv", "kind": "text"},
        {"key": "doi", "label": "DOI (kun)", "kind": "text"},
        {"key": "efficiency_status", "label": "Holat", "kind": "text"},
    ]

    formatted_rows = [
        {
            "store": r["store"],
            "date": period_label,
            "name": r["name"],
            "sku": r["sku"],
            "barcode": r["barcode"],
            "category": r["category"],
            "brand": r["brand"],
            "unit": r["unit"],
            "current_stock": r["current_stock"],
            "sold_qty": r["sold_qty"],
            "returned_qty": r["returned_qty"],
            "net_sold_qty": r["net_sold_qty"],
            "gross_sales": _money(r["gross_sales"]),
            "discount": _money(r["discount"]),
            "net_revenue": _money(r["net_revenue"]),
            "revenue_share_pct": f"{r['revenue_share_pct']:.1f}%",
            "unit_cost": _money(r["unit_cost"]),
            "total_cost": _money(r["total_cost"]),
            "profit": _money(r["profit"]),
            "margin_pct": f"{r['margin_pct']:.1f}%",
            "sales_velocity": f"{r['sales_velocity']:.2f}",
            "doi": f"{r['doi']:.1f}" if r["doi"] is not None else "—",
            "efficiency_status": r["efficiency_status_display"],
        }
        for r in raw_rows
    ]

    summary = [
        {"label": "Davr", "value": period_label, "kind": "text"},
        {"label": "Tovarlar soni", "value": totals["count"], "kind": "int"},
        {"label": "Jami joriy qoldiq", "value": totals["total_current_stock"], "kind": "int"},
        {"label": "Sof sotilgan", "value": totals["total_net_sold_qty"], "kind": "int"},
        {"label": "Chegirmagacha savdo", "value": _money(totals["total_gross_sales"]), "kind": "money"},
        {"label": "Jami chegirma", "value": _money(totals["total_discount"]), "kind": "money"},
        {"label": "Sof tushum", "value": _money(totals["total_net_revenue"]), "kind": "money"},
        {"label": "Jami tannarx", "value": _money(totals["total_cost"]), "kind": "money"},
        {"label": "Sof foyda", "value": _money(totals["total_profit"]), "kind": "money"},
        {"label": "O'rtacha marja", "value": f"{totals['overall_margin']:.1f}%", "kind": "text"},
        {"label": "Faol tovarlar", "value": totals["active_count"], "kind": "int"},
        {"label": "Harakatsiz tovarlar (Dead stock)", "value": totals["dead_stock_count"], "kind": "int"},
        {"label": "Tugagan tovarlar (Out of stock)", "value": totals["out_of_stock_count"], "kind": "int"},
    ]

    if totals.get("net_return_count", 0) > 0:
        summary.append({
            "label": "Qaytarim ustun tovarlar",
            "value": totals["net_return_count"],
            "kind": "int",
        })

    if totals["any_missing_cost"]:
        summary.append({
            "label": "Diqqat",
            "value": "Ba'zi tovarlarda tannarx yo'q — foyda taxminiy",
            "kind": "text",
        })

    return columns, formatted_rows, None, summary


def _build_abc_analysis(params, store_id):
    """
    ABC Tahlili hisoboti (Pareto 80/15/5):
    Tovar assortimentini daromad (revenue), sof foyda (profit) yoki
    sotilgan miqdor (quantity) bo'yicha A, B va C toifalarga ajratadi.
    """
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)
    period_label = f"{d_from.strftime('%d.%m.%Y')} — {d_to.strftime('%d.%m.%Y')}"

    category_id = int(params["category_id"]) if (params.get("category_id") or "").isdigit() else None
    brand_id = int(params["brand_id"]) if (params.get("brand_id") or "").isdigit() else None
    product_id = int(params["product_id"]) if (params.get("product_id") or "").isdigit() else None
    seller_id = int(params["seller_id"]) if (params.get("seller_id") or "").isdigit() else None
    customer_id = int(params["customer_id"]) if (params.get("customer_id") or "").isdigit() else None
    sku = params.get("sku")
    barcode = params.get("barcode")
    search = params.get("search")
    metric = (params.get("metric") or "revenue").strip().lower()
    abc_class = params.get("abc_class")

    raw_rows, totals = ReportingFoundationService.get_abc_analysis_metrics(
        start=start,
        end=end,
        store_id=store_id,
        category_id=category_id,
        brand_id=brand_id,
        product_id=product_id,
        sku=sku,
        barcode=barcode,
        seller_id=seller_id,
        customer_id=customer_id,
        search=search,
        metric=metric,
        abc_class=abc_class,
    )

    # Ixtiyoriy saralash (sukut bo'yicha tabiiy Pareto tartibi)
    sort_by = (params.get("sort_by") or "pareto").strip().lower()
    sort_dir = (params.get("sort_dir") or "desc").strip().lower()
    reverse = sort_dir != "asc"

    if sort_by == "revenue":
        raw_rows.sort(key=lambda x: (x["net_revenue"], (x.get("name") or "").lower()), reverse=reverse)
    elif sort_by == "profit":
        raw_rows.sort(key=lambda x: (x["profit"], (x.get("name") or "").lower()), reverse=reverse)
    elif sort_by == "quantity":
        raw_rows.sort(key=lambda x: (x["net_sold_qty"], (x.get("name") or "").lower()), reverse=reverse)
    elif sort_by == "share":
        raw_rows.sort(key=lambda x: (x["share_pct"], (x.get("name") or "").lower()), reverse=reverse)
    elif sort_by == "cumulative":
        raw_rows.sort(key=lambda x: (x["cumulative_pct"], (x.get("name") or "").lower()), reverse=reverse)
    elif sort_by == "name":
        raw_rows.sort(key=lambda x: (x.get("name") or "").lower(), reverse=reverse)
    elif sort_by == "abc_class":
        raw_rows.sort(key=lambda x: (x["abc_class"], -x["metric_value"]), reverse=reverse)

    metric_label = (
        "Sof tushum" if metric == "revenue"
        else "Sof foyda" if metric == "profit"
        else "Sof miqdor"
    )

    columns = [
        {"key": "store", "label": "Do'kon", "kind": "text"},
        {"key": "abc_class", "label": "ABC toifasi", "kind": "badge"},
        {"key": "share_pct", "label": "Ulush %", "kind": "percent"},
        {"key": "cumulative_pct", "label": "Kumulyativ %", "kind": "percent"},
        {"key": "metric_value", "label": f"{metric_label} (Tanlangan)", "kind": "money" if metric in ("revenue", "profit") else "number"},
        {"key": "name", "label": "Tovar nomi", "kind": "text"},
        {"key": "sku", "label": "Artikul (SKU)", "kind": "text"},
        {"key": "barcode", "label": "Shtrixkod", "kind": "text"},
        {"key": "category", "label": "Kategoriya", "kind": "text"},
        {"key": "brand", "label": "Brend", "kind": "text"},
        {"key": "unit", "label": "Birlik", "kind": "text"},
        {"key": "sold_qty", "label": "Sotilgan miqdor", "kind": "number"},
        {"key": "returned_qty", "label": "Qaytarilgan miqdor", "kind": "number"},
        {"key": "net_sold_qty", "label": "Sof sotuv miqdori", "kind": "number"},
        {"key": "gross_sales", "label": "Yalpi tushum", "kind": "money"},
        {"key": "discount", "label": "Chegirma", "kind": "money"},
        {"key": "net_revenue", "label": "Sof tushum", "kind": "money"},
        {"key": "unit_cost", "label": "Birlik tannarxi", "kind": "money"},
        {"key": "total_cost", "label": "Jami tannarx", "kind": "money"},
        {"key": "profit", "label": "Sof foyda", "kind": "money"},
        {"key": "margin_pct", "label": "Marja %", "kind": "percent"},
    ]

    formatted_rows = [
        {
            "store_id": r.get("store_id"),
            "store": r.get("store") or "-",
            "product_id": r.get("product_id"),
            "name": r.get("name") or "-",
            "sku": r.get("sku") or "-",
            "barcode": r.get("barcode") or "-",
            "category": r.get("category") or "-",
            "brand": r.get("brand") or "-",
            "unit": r.get("unit") or "-",
            "abc_class": r.get("abc_class") or "C",
            "share_pct": f"{r.get('share_pct', 0.0):.2f}%",
            "cumulative_pct": f"{r.get('cumulative_pct', 0.0):.2f}%",
            "metric_value": _money(r["metric_value"]) if metric in ("revenue", "profit") else f"{r['metric_value']:.2f}",
            "sold_qty": str(r.get("sold_qty", 0)),
            "returned_qty": str(r.get("returned_qty", 0)),
            "net_sold_qty": str(r.get("net_sold_qty", 0)),
            "gross_sales": _money(r.get("gross_sales", 0)),
            "discount": _money(r.get("discount", 0)),
            "net_revenue": _money(r.get("net_revenue", 0)),
            "unit_cost": _money(r.get("unit_cost", 0)),
            "total_cost": _money(r.get("total_cost", 0)),
            "profit": _money(r.get("profit", 0)),
            "margin_pct": f"{r.get('margin_pct', 0.0):.1f}%",
        }
        for r in raw_rows
    ]

    summary = [
        {"label": "Davr", "value": period_label, "kind": "text"},
        {
            "label": "A toifa (80% lokomotiv)",
            "value": f"{totals['count_a']} ta ({totals['pct_count_a']}%)",
            "kind": "text",
            "hint": f"Ko'rsatkichdagi hissasi: {totals['share_a_metric']}%",
        },
        {
            "label": "B toifa (15% o'rta)",
            "value": f"{totals['count_b']} ta ({totals['pct_count_b']}%)",
            "kind": "text",
            "hint": f"Ko'rsatkichdagi hissasi: {totals['share_b_metric']}%",
        },
        {
            "label": "C toifa (5% quyi / nol / manfiy)",
            "value": f"{totals['count_c']} ta ({totals['pct_count_c']}%)",
            "kind": "text",
            "hint": f"Ko'rsatkichdagi hissasi: {totals['share_c_metric']}%",
        },
        {
            "label": "Jami musbat ko'rsatkich",
            "value": _money(totals["total_positive_metric"]) if metric in ("revenue", "profit") else f"{totals['total_positive_metric']:.2f}",
            "kind": "money" if metric in ("revenue", "profit") else "number",
        },
    ]

    return columns, formatted_rows, None, summary


def _build_inventory_results(params, store_id):
    """
    Inventarizatsiya natijalari va kamomad/ortiqcha tahlili (Phase 1.4).
    """
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)
    period_label = f"{d_from.strftime('%d.%m.%Y')} — {d_to.strftime('%d.%m.%Y')}"

    session_id = int(params["session_id"]) if (params.get("session_id") or "").isdigit() else None
    product_id = int(params["product_id"]) if (params.get("product_id") or "").isdigit() else None
    category_id = int(params["category_id"]) if (params.get("category_id") or "").isdigit() else None
    brand_id = int(params["brand_id"]) if (params.get("brand_id") or "").isdigit() else None
    sku = params.get("sku")
    barcode = params.get("barcode")
    status = params.get("status")
    search = params.get("search")

    raw_rows, totals = ReportingFoundationService.get_inventory_results_metrics(
        start=start,
        end=end,
        store_id=store_id,
        session_id=session_id,
        product_id=product_id,
        sku=sku,
        barcode=barcode,
        category_id=category_id,
        brand_id=brand_id,
        status=status,
        search=search,
    )

    # Saralash
    sort_by = (params.get("sort_by") or "session_id").strip().lower()
    sort_dir = (params.get("sort_dir") or "desc").strip().lower()
    reverse = sort_dir != "asc"

    if sort_by == "difference":
        null_val = float("-inf") if reverse else float("inf")
        raw_rows.sort(key=lambda x: (x["difference_qty"] if x["difference_qty"] is not None else null_val), reverse=reverse)
    elif sort_by == "shortage_qty":
        raw_rows.sort(key=lambda x: x["shortage_qty"], reverse=reverse)
    elif sort_by == "excess_qty":
        raw_rows.sort(key=lambda x: x["excess_qty"], reverse=reverse)
    elif sort_by == "shortage_value":
        raw_rows.sort(key=lambda x: x["shortage_value"], reverse=reverse)
    elif sort_by == "excess_value":
        raw_rows.sort(key=lambda x: x["excess_value"], reverse=reverse)
    elif sort_by == "name":
        raw_rows.sort(key=lambda x: (x.get("product_name") or "").lower(), reverse=reverse)
    elif sort_by == "status":
        raw_rows.sort(key=lambda x: x["status"], reverse=reverse)
    elif sort_by == "expected_qty":
        raw_rows.sort(key=lambda x: x["expected_qty"], reverse=reverse)
    elif sort_by == "counted_qty":
        null_val = float("-inf") if reverse else float("inf")
        raw_rows.sort(key=lambda x: (x["counted_qty"] if x["counted_qty"] is not None else null_val), reverse=reverse)
    else:  # session_id
        raw_rows.sort(key=lambda x: (x["session_id"], (x.get("product_name") or "").lower()), reverse=reverse)

    STATUS_DISPLAY = {
        "matched": "Mos kelgan",
        "shortage": "Kamomad",
        "excess": "Ortiqcha",
        "unchecked": "Sanalmagan",
    }

    columns = [
        {"key": "session_id", "label": "Sessiya ID", "kind": "int"},
        {"key": "store_name", "label": "Do'kon", "kind": "text"},
        {"key": "session_date", "label": "Sana", "kind": "text"},
        {"key": "product_name", "label": "Tovar nomi", "kind": "text"},
        {"key": "sku", "label": "SKU", "kind": "text"},
        {"key": "barcode", "label": "Shtrix-kod", "kind": "text"},
        {"key": "unit", "label": "O'lchov", "kind": "text"},
        {"key": "category_name", "label": "Kategoriya", "kind": "text"},
        {"key": "brand_name", "label": "Brend", "kind": "text"},
        {"key": "expected_qty", "label": "Kutilgan qoldiq", "kind": "number"},
        {"key": "counted_qty", "label": "Sanalgan miqdor", "kind": "number"},
        {"key": "difference_qty", "label": "Tafovut miqdori", "kind": "number"},
        {"key": "shortage_qty", "label": "Kamomad miqdori", "kind": "number"},
        {"key": "excess_qty", "label": "Ortiqcha miqdori", "kind": "number"},
        {"key": "unit_cost", "label": "Birlik tannarxi", "kind": "money"},
        {"key": "shortage_value", "label": "Kamomad summasi", "kind": "money"},
        {"key": "excess_value", "label": "Ortiqcha summasi", "kind": "money"},
        {"key": "final_balance", "label": "Yakuniy hisobiy qoldiq", "kind": "number"},
        {"key": "status", "label": "Holati", "kind": "badge"},
    ]

    formatted_rows = [
        {
            "session_id": r["session_id"],
            "store_id": r.get("store_id"),
            "store_name": r.get("store_name") or "-",
            "session_date": r.get("session_date") or "-",
            "product_id": r.get("product_id"),
            "product_name": r.get("product_name") or "-",
            "sku": r.get("sku") or "-",
            "barcode": r.get("barcode") or "-",
            "unit": r.get("unit") or "-",
            "category_name": r.get("category_name") or "-",
            "brand_name": r.get("brand_name") or "-",
            "expected_qty": r["expected_qty"],
            "counted_qty": r["counted_qty"] if r["counted_qty"] is not None else "—",
            "difference_qty": r["difference_qty"] if r["difference_qty"] is not None else "—",
            "shortage_qty": r["shortage_qty"],
            "excess_qty": r["excess_qty"],
            "unit_cost": _money(r["unit_cost"]),
            "shortage_value": _money(r["shortage_value"]),
            "excess_value": _money(r["excess_value"]),
            "final_balance": r["final_balance"],
            "status": STATUS_DISPLAY.get(r["status"], r["status"]),
            "raw_status": r["status"],
        }
        for r in raw_rows
    ]

    summary = [
        {"label": "Davr", "value": period_label, "kind": "text"},
        {"label": "Jami kutilgan qoldiq", "value": totals["total_expected_qty"], "kind": "number"},
        {"label": "Jami sanalgan qoldiq", "value": totals["total_counted_qty"], "kind": "number"},
        {"label": "Jami kamomad miqdori", "value": totals["total_shortage_qty"], "kind": "number"},
        {"label": "Jami kamomad summasi", "value": _money(totals["total_shortage_value"]), "kind": "money"},
        {"label": "Jami ortiqcha miqdori", "value": totals["total_excess_qty"], "kind": "number"},
        {"label": "Jami ortiqcha summasi", "value": _money(totals["total_excess_value"]), "kind": "money"},
        {"label": "Sof tafovut qiymati (Ortiqcha - Kamomad)", "value": _money(totals["net_difference_value"]), "kind": "money"},
        {"label": "Mos kelgan tovarlar", "value": f"{totals['matched_count']} ta", "kind": "text"},
        {"label": "Kamomadli tovarlar", "value": f"{totals['shortage_count']} ta", "kind": "text"},
        {"label": "Ortiqchali tovarlar", "value": f"{totals['excess_count']} ta", "kind": "text"},
        {"label": "Sanalmagan tovarlar", "value": f"{totals['unchecked_count']} ta", "kind": "text"},
    ]

    return columns, formatted_rows, None, summary


def _build_order_returns(params, store_id):
    """
    Buyurtma qaytarishlari hisoboti (Phase 1.5).
    """
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)
    period_label = f"{d_from.strftime('%d.%m.%Y')} — {d_to.strftime('%d.%m.%Y')}"

    return_id = int(params["return_id"]) if (params.get("return_id") or "").isdigit() else None
    order_id = int(params["order_id"]) if (params.get("order_id") or "").isdigit() else None
    product_id = int(params["product_id"]) if (params.get("product_id") or "").isdigit() else None
    category_id = int(params["category_id"]) if (params.get("category_id") or "").isdigit() else None
    brand_id = int(params["brand_id"]) if (params.get("brand_id") or "").isdigit() else None
    supplier_id = int(params["supplier_id"]) if (params.get("supplier_id") or "").isdigit() else None
    seller_id = int(params["seller_id"]) if (params.get("seller_id") or "").isdigit() else None
    sku = params.get("sku")
    barcode = params.get("barcode")
    search = params.get("search")

    raw_rows, totals = ReportingFoundationService.get_order_returns_metrics(
        start=start,
        end=end,
        store_id=store_id,
        return_id=return_id,
        order_id=order_id,
        product_id=product_id,
        sku=sku,
        barcode=barcode,
        category_id=category_id,
        brand_id=brand_id,
        supplier_id=supplier_id,
        seller_id=seller_id,
        search=search,
    )

    # Saralash
    sort_by = (params.get("sort_by") or "return_datetime").strip().lower()
    sort_dir = (params.get("sort_dir") or "desc").strip().lower()
    reverse = sort_dir != "asc"

    if sort_by == "returned_qty":
        raw_rows.sort(key=lambda x: x["returned_qty"], reverse=reverse)
    elif sort_by == "refund_amount":
        raw_rows.sort(key=lambda x: x["refund_amount"], reverse=reverse)
    elif sort_by == "profit_impact":
        raw_rows.sort(key=lambda x: x["profit_impact"], reverse=reverse)
    elif sort_by == "name":
        raw_rows.sort(key=lambda x: (x.get("product_name") or "").lower(), reverse=reverse)
    elif sort_by == "order_id":
        raw_rows.sort(key=lambda x: x["order_id"], reverse=reverse)
    elif sort_by == "return_id":
        raw_rows.sort(key=lambda x: x["return_id"], reverse=reverse)
    else:  # return_datetime
        raw_rows.sort(key=lambda x: (x.get("return_timestamp") or "", x["return_id"]), reverse=reverse)

    columns = [
        {"key": "return_id", "label": "Qaytarish ID", "kind": "int"},
        {"key": "order_id", "label": "Buyurtma ID", "kind": "int"},
        {"key": "store_name", "label": "Filial", "kind": "text"},
        {"key": "return_datetime", "label": "Qaytarish sanasi", "kind": "text"},
        {"key": "seller_name", "label": "Sotuvchi", "kind": "text"},
        {"key": "customer_name", "label": "Mijoz", "kind": "text"},
        {"key": "product_name", "label": "Tovar nomi", "kind": "text"},
        {"key": "sku", "label": "SKU", "kind": "text"},
        {"key": "barcode", "label": "Shtrix-kod", "kind": "text"},
        {"key": "brand_name", "label": "Brend", "kind": "text"},
        {"key": "category_name", "label": "Kategoriya", "kind": "text"},
        {"key": "unit", "label": "O'lchov", "kind": "text"},
        {"key": "supplier_name", "label": "Ta'minotchi", "kind": "text"},
        {"key": "returned_qty", "label": "Qaytarilgan miqdor", "kind": "number"},
        {"key": "unit_sale_price", "label": "Birlik sotuv narxi", "kind": "money"},
        {"key": "unit_purchase_price", "label": "Birlik tannarxi", "kind": "money"},
        {"key": "sale_value", "label": "Sotuv qiymati", "kind": "money"},
        {"key": "discount_refunded", "label": "Qaytarilgan chegirma", "kind": "money"},
        {"key": "refund_amount", "label": "Qaytarilgan summa", "kind": "money"},
        {"key": "purchase_value", "label": "Tannarx qiymati", "kind": "money"},
        {"key": "profit_impact", "label": "Yo'qotilgan foyda", "kind": "money"},
        {"key": "payment_method", "label": "To'lov usuli", "kind": "badge"},
        {"key": "comment", "label": "Izoh", "kind": "text"},
    ]

    formatted_rows = [
        {
            "return_id": r["return_id"],
            "order_id": r["order_id"],
            "store_id": r.get("store_id"),
            "store_name": r.get("store_name") or "-",
            "return_datetime": r.get("return_datetime") or "-",
            "seller_id": r.get("seller_id"),
            "seller_name": r.get("seller_name") or "-",
            "customer_id": r.get("customer_id"),
            "customer_name": r.get("customer_name") or "—",
            "product_id": r.get("product_id"),
            "product_name": r.get("product_name") or "-",
            "sku": r.get("sku") or "-",
            "barcode": r.get("barcode") or "-",
            "brand_id": r.get("brand_id"),
            "brand_name": r.get("brand_name") or "-",
            "category_id": r.get("category_id"),
            "category_name": r.get("category_name") or "-",
            "unit": r.get("unit") or "dona",
            "supplier_name": r.get("supplier_name") or "-",
            "returned_qty": r["returned_qty"],
            "unit_sale_price": _money(r["unit_sale_price"]),
            "unit_purchase_price": _money(r["unit_purchase_price"]),
            "sale_value": _money(r["sale_value"]),
            "discount_refunded": _money(r["discount_refunded"]),
            "refund_amount": _money(r["refund_amount"]),
            "purchase_value": _money(r["purchase_value"]),
            "profit_impact": _money(r["profit_impact"]),
            "payment_method": r.get("payment_method") or "-",
            "comment": r.get("comment") or "",
        }
        for r in raw_rows
    ]

    summary = [
        {"label": "Qaytarishlar soni", "value": totals["total_returns_count"], "kind": "int"},
        {"label": "Qaytarilgan tovarlar", "value": totals["total_returned_qty"], "kind": "number"},
        {"label": "Qaytarilgan summa", "value": _money(totals["total_refund_amount"]), "kind": "money"},
        {"label": "Sotuv qiymati", "value": _money(totals["total_sale_value"]), "kind": "money"},
        {"label": "Qaytarilgan chegirma", "value": _money(totals["total_discount_refunded"]), "kind": "money"},
        {"label": "Tannarx qiymati", "value": _money(totals["total_purchase_value"]), "kind": "money"},
        {"label": "Yo'qotilgan foyda", "value": _money(totals["total_profit_impact"]), "kind": "money"},
    ]

    info = {
        "title": "Buyurtma qaytarishlari hisoboti",
        "description": f"Tanlangan davr: {period_label}. Jami {totals['total_returns_count']} ta qaytarish operatsiyasi ({totals['total_rows']} ta tovar qatori).",
    }
    if totals.get("has_zero_cost_items"):
        info["warning"] = "Ayrim tovarlarda partiya tannarxi 0 bo'lgani sababli tannarx qiymati va yo'qotilgan foyda indikativ bo'lishi mumkin."

    return columns, formatted_rows, None, summary, info


def _build_write_offs(params, store_id):
    """
    Hisobdan chiqarishlar hisoboti (Phase 1.6).
    """
    default_all = bool(params.get("write_off_id") or not (params.get("from") or params.get("to")))
    d_from, d_to = _parse_dates(params, default_all=default_all)
    start, end = _dt_bounds(d_from, d_to)
    period_label = _period_label(params, d_from, d_to)

    write_off_id = int(params["write_off_id"]) if (params.get("write_off_id") or "").isdigit() else None
    reason = params.get("reason")
    product_id = int(params["product_id"]) if (params.get("product_id") or "").isdigit() else None
    category_id = int(params["category_id"]) if (params.get("category_id") or "").isdigit() else None
    brand_id = int(params["brand_id"]) if (params.get("brand_id") or "").isdigit() else None
    supplier_id = int(params["supplier_id"]) if (params.get("supplier_id") or "").isdigit() else None
    user_id = int(params["user_id"]) if (params.get("user_id") or "").isdigit() else None
    inventory_session_id = int(params["inventory_session_id"]) if (params.get("inventory_session_id") or "").isdigit() else None
    sku = params.get("sku")
    barcode = params.get("barcode")
    search = params.get("search")

    raw_rows, totals = ReportingFoundationService.get_write_offs_metrics(
        start=start,
        end=end,
        store_id=store_id,
        write_off_id=write_off_id,
        reason=reason,
        product_id=product_id,
        sku=sku,
        barcode=barcode,
        category_id=category_id,
        brand_id=brand_id,
        supplier_id=supplier_id,
        user_id=user_id,
        inventory_session_id=inventory_session_id,
        search=search,
    )

    # Saralash
    sort_by = (params.get("sort_by") or "write_off_datetime").strip().lower()
    sort_dir = (params.get("sort_dir") or "desc").strip().lower()
    reverse = sort_dir != "asc"

    if sort_by == "quantity":
        raw_rows.sort(key=lambda x: x["quantity"], reverse=reverse)
    elif sort_by == "purchase_value":
        raw_rows.sort(key=lambda x: x["purchase_value"], reverse=reverse)
    elif sort_by == "sale_value":
        raw_rows.sort(key=lambda x: x["sale_value"], reverse=reverse)
    elif sort_by == "profit_impact":
        raw_rows.sort(key=lambda x: x["profit_impact"], reverse=reverse)
    elif sort_by in ("product_name", "name"):
        raw_rows.sort(key=lambda x: (x.get("product_name") or "").lower(), reverse=reverse)
    elif sort_by == "write_off_id":
        raw_rows.sort(key=lambda x: x["write_off_id"], reverse=reverse)
    elif sort_by == "reason":
        raw_rows.sort(key=lambda x: (x.get("reason_display") or "").lower(), reverse=reverse)
    else:  # write_off_datetime
        raw_rows.sort(key=lambda x: (x.get("write_off_timestamp") or "", x["write_off_id"]), reverse=reverse)

    columns = [
        {"key": "write_off_id", "label": "Hujjat №", "kind": "int"},
        {"key": "store_name", "label": "Filial", "kind": "text"},
        {"key": "write_off_datetime", "label": "Sana", "kind": "text"},
        {"key": "reason_display", "label": "Sabab", "kind": "badge"},
        {"key": "created_by_name", "label": "Mas'ul xodim", "kind": "text"},
        {"key": "product_name", "label": "Tovar nomi", "kind": "text"},
        {"key": "sku", "label": "SKU", "kind": "text"},
        {"key": "barcode", "label": "Shtrix-kod", "kind": "text"},
        {"key": "category_name", "label": "Kategoriya", "kind": "text"},
        {"key": "brand_name", "label": "Brend", "kind": "text"},
        {"key": "unit", "label": "O'lchov", "kind": "text"},
        {"key": "supplier_name", "label": "Ta'minotchi", "kind": "text"},
        {"key": "product_status", "label": "Tovar holati", "kind": "badge"},
        {"key": "quantity", "label": "Miqdor", "kind": "number"},
        {"key": "unit_purchase_price", "label": "Tannarx (birlik)", "kind": "money"},
        {"key": "unit_sale_price", "label": "Sotuv narxi (birlik)", "kind": "money"},
        {"key": "purchase_value", "label": "Tannarx summasi", "kind": "money"},
        {"key": "sale_value", "label": "Sotuv summasi", "kind": "money"},
        {"key": "profit_impact", "label": "Yo'qotilgan foyda", "kind": "money"},
        {"key": "inventory_session_id", "label": "Inventarizatsiya №", "kind": "int"},
        {"key": "comment", "label": "Izoh", "kind": "text"},
    ]

    formatted_rows = [
        {
            "write_off_id": r["write_off_id"],
            "store_id": r.get("store_id"),
            "store_name": r.get("store_name") or "-",
            "write_off_datetime": r.get("write_off_datetime") or "-",
            "reason": r.get("reason"),
            "reason_display": r.get("reason_display") or "-",
            "created_by_id": r.get("created_by_id"),
            "created_by_name": r.get("created_by_name") or "-",
            "product_id": r.get("product_id"),
            "product_name": r.get("product_name") or "-",
            "sku": r.get("sku") or "-",
            "barcode": r.get("barcode") or "-",
            "brand_id": r.get("brand_id"),
            "brand_name": r.get("brand_name") or "-",
            "category_id": r.get("category_id"),
            "category_name": r.get("category_name") or "-",
            "unit": r.get("unit") or "dona",
            "supplier_name": r.get("supplier_name") or "-",
            "product_status": r.get("product_status") or "-",
            "quantity": r["quantity"],
            "unit_purchase_price": _money(r["unit_purchase_price"]),
            "unit_sale_price": _money(r["unit_sale_price"]),
            "purchase_value": _money(r["purchase_value"]),
            "sale_value": _money(r["sale_value"]),
            "profit_impact": _money(r["profit_impact"]),
            "inventory_session_id": r.get("inventory_session_id") if r.get("inventory_session_id") is not None else "-",
            "comment": r.get("comment") or "",
        }
        for r in raw_rows
    ]

    summary = [
        {"label": "Hujjatlar soni", "value": totals["total_write_offs_count"], "kind": "int"},
        {"label": "Chiqarilgan tovarlar", "value": totals["total_written_off_qty"], "kind": "number"},
        {"label": "Tannarx summasi", "value": _money(totals["total_purchase_value"]), "kind": "money"},
        {"label": "Sotuv summasi", "value": _money(totals["total_sale_value"]), "kind": "money"},
        {"label": "Yo'qotilgan foyda", "value": _money(totals["total_profit_impact"]), "kind": "money"},
    ]

    info = {
        "title": "Hisobdan chiqarishlar hisoboti",
        "description": f"Tanlangan davr: {period_label}. Jami {totals['total_write_offs_count']} ta hisobdan chiqarish hujjati ({totals['total_rows']} ta tovar qatori).",
    }
    if totals.get("has_zero_cost_items"):
        info["warning"] = "Ayrim tovarlarda tannarx 0 bo'lgani sababli tannarx summasi va yo'qotilgan foyda indikativ bo'lishi mumkin."

    return columns, formatted_rows, None, summary, info


def _build_imports(params, store_id):
    """
    Kirimlar (Importlar) hisoboti (Phase 1.7).
    """
    default_all = bool(params.get("entry_id") or not (params.get("from") or params.get("to")))
    d_from, d_to = _parse_dates(params, default_all=default_all)
    start, end = _dt_bounds(d_from, d_to)
    period_label = _period_label(params, d_from, d_to)

    entry_id = int(params["entry_id"]) if (params.get("entry_id") or "").isdigit() else None
    supplier_id = int(params["supplier_id"]) if (params.get("supplier_id") or "").isdigit() else None
    payment_status = params.get("payment_status")
    user_id = int(params["user_id"]) if (params.get("user_id") or "").isdigit() else None
    product_id = int(params["product_id"]) if (params.get("product_id") or "").isdigit() else None
    category_id = int(params["category_id"]) if (params.get("category_id") or "").isdigit() else None
    brand_id = int(params["brand_id"]) if (params.get("brand_id") or "").isdigit() else None
    has_returns = params.get("has_returns")
    sku = params.get("sku")
    barcode = params.get("barcode")
    search = params.get("search")

    raw_rows, totals = ReportingFoundationService.get_imports_metrics(
        start=start,
        end=end,
        store_id=store_id,
        entry_id=entry_id,
        supplier_id=supplier_id,
        payment_status=payment_status,
        user_id=user_id,
        product_id=product_id,
        sku=sku,
        barcode=barcode,
        category_id=category_id,
        brand_id=brand_id,
        has_returns=has_returns,
        search=search,
    )

    # Saralash
    sort_by = (params.get("sort_by") or "entry_datetime").strip().lower()
    sort_dir = (params.get("sort_dir") or "desc").strip().lower()
    reverse = sort_dir != "asc"

    if sort_by == "quantity":
        raw_rows.sort(key=lambda x: x["quantity"], reverse=reverse)
    elif sort_by == "purchase_value":
        raw_rows.sort(key=lambda x: x["purchase_value"], reverse=reverse)
    elif sort_by == "sale_value":
        raw_rows.sort(key=lambda x: x["sale_value"], reverse=reverse)
    elif sort_by == "potential_margin":
        raw_rows.sort(key=lambda x: x["potential_margin"], reverse=reverse)
    elif sort_by == "net_purchase_value":
        raw_rows.sort(key=lambda x: x["net_purchase_value"], reverse=reverse)
    elif sort_by in ("product_name", "name"):
        raw_rows.sort(key=lambda x: (x.get("product_name") or "").lower(), reverse=reverse)
    elif sort_by == "supplier_name":
        raw_rows.sort(key=lambda x: (x.get("supplier_name") or "").lower(), reverse=reverse)
    elif sort_by == "entry_id":
        raw_rows.sort(key=lambda x: x["entry_id"], reverse=reverse)
    else:  # entry_datetime
        raw_rows.sort(key=lambda x: (x.get("entry_timestamp") or "", x["entry_id"]), reverse=reverse)

    columns = [
        {"key": "entry_id", "label": "Hujjat №", "kind": "int"},
        {"key": "store_name", "label": "Filial", "kind": "text"},
        {"key": "entry_datetime", "label": "Sana", "kind": "text"},
        {"key": "supplier_name", "label": "Ta'minotchi", "kind": "text"},
        {"key": "created_by_name", "label": "Mas'ul xodim", "kind": "text"},
        {"key": "product_name", "label": "Tovar nomi", "kind": "text"},
        {"key": "sku", "label": "SKU", "kind": "text"},
        {"key": "barcode", "label": "Shtrix-kod", "kind": "text"},
        {"key": "category_name", "label": "Kategoriya", "kind": "text"},
        {"key": "brand_name", "label": "Brend", "kind": "text"},
        {"key": "unit", "label": "O'lchov", "kind": "text"},
        {"key": "product_status", "label": "Tovar holati", "kind": "badge"},
        {"key": "quantity", "label": "Qabul miqdori", "kind": "number"},
        {"key": "unit_purchase_price", "label": "Tannarx (birlik)", "kind": "money"},
        {"key": "unit_sale_price", "label": "Sotuv narxi (birlik)", "kind": "money"},
        {"key": "unit_wholesale_price", "label": "Ulgurji narx (birlik)", "kind": "money"},
        {"key": "purchase_value", "label": "Jami xarid summasi", "kind": "money"},
        {"key": "sale_value", "label": "Jami sotuv qiymati", "kind": "money"},
        {"key": "potential_margin", "label": "Kutilayotgan foyda", "kind": "money"},
        {"key": "returned_qty", "label": "Qaytarilgan miqdor", "kind": "number"},
        {"key": "returned_value", "label": "Qaytarilgan summa", "kind": "money"},
        {"key": "net_quantity", "label": "Sof qabul miqdori", "kind": "number"},
        {"key": "net_purchase_value", "label": "Sof xarid summasi", "kind": "money"},
        {"key": "payment_status", "label": "To'lov holati", "kind": "badge"},
        {"key": "comment", "label": "Izoh", "kind": "text"},
    ]

    payment_status_display_map = {
        "paid": "To'langan",
        "partial": "Qisman to'langan",
        "unpaid": "To'lanmagan",
    }

    formatted_rows = [
        {
            "entry_id": r["entry_id"],
            "store_id": r.get("store_id"),
            "store_name": r.get("store_name") or "-",
            "entry_datetime": r.get("entry_datetime") or "-",
            "supplier_id": r.get("supplier_id"),
            "supplier_name": r.get("supplier_name") or "-",
            "created_by_id": r.get("created_by_id"),
            "created_by_name": r.get("created_by_name") or "-",
            "product_id": r.get("product_id"),
            "product_name": r.get("product_name") or "-",
            "sku": r.get("sku") or "-",
            "barcode": r.get("barcode") or "-",
            "category_id": r.get("category_id"),
            "category_name": r.get("category_name") or "-",
            "brand_id": r.get("brand_id"),
            "brand_name": r.get("brand_name") or "-",
            "unit": r.get("unit") or "dona",
            "product_status": r.get("product_status") or "-",
            "quantity": r["quantity"],
            "unit_purchase_price": _money(r["unit_purchase_price"]),
            "unit_sale_price": _money(r["unit_sale_price"]),
            "unit_wholesale_price": _money(r["unit_wholesale_price"]),
            "purchase_value": _money(r["purchase_value"]),
            "sale_value": _money(r["sale_value"]),
            "potential_margin": _money(r["potential_margin"]),
            "returned_qty": r["returned_qty"],
            "returned_value": _money(r["returned_value"]),
            "net_quantity": r["net_quantity"],
            "net_purchase_value": _money(r["net_purchase_value"]),
            "payment_status": payment_status_display_map.get(r["payment_status"], r["payment_status"]),
            "raw_payment_status": r["payment_status"],
            "comment": r.get("comment") or "",
        }
        for r in raw_rows
    ]

    summary = [
        {"label": "Kirim hujjatlari soni", "value": totals["total_entries_count"], "kind": "int"},
        {"label": "Jami tovar qatorlari", "value": totals["total_rows"], "kind": "int"},
        {"label": "Qabul qilingan tovarlar", "value": totals["total_received_qty"], "kind": "number"},
        {"label": "Jami xarid qiymati", "value": _money(totals["total_purchase_value"]), "kind": "money"},
        {"label": "Jami sotuv qiymati", "value": _money(totals["total_sale_value"]), "kind": "money"},
        {"label": "Kutilayotgan foyda", "value": _money(totals["total_potential_margin"]), "kind": "money"},
        {"label": "Qaytarilgan tovarlar", "value": totals["total_returned_qty"], "kind": "number"},
        {"label": "Qaytarilgan summa", "value": _money(totals["total_returned_value"]), "kind": "money"},
        {"label": "Sof xarid summasi", "value": _money(totals["total_net_purchase_value"]), "kind": "money"},
    ]

    info = {
        "title": "Kirimlar (Importlar) hisoboti",
        "description": f"Tanlangan davr: {period_label}. Jami {totals['total_entries_count']} ta kirim hujjati ({totals['total_rows']} ta tovar qatori).",
    }
    if totals.get("has_zero_cost_items"):
        info["warning"] = "Ayrim tovarlarda tannarx 0 bo'lgani sababli tannarx summasi va kutilayotgan foyda indikativ bo'lishi mumkin."

    return columns, formatted_rows, None, summary, info


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


def _last_supplier_map(product_ids, before=None, store_id=None) -> dict:
    """
    product_id -> oxirgi kirim (StockEntry) ta'minotchisi nomi.

    ARXITEKTURA VA MA'LUMOTLAR MODELI CHEKLOVI (Phase 0):
    AutoCRM da SaleItem va ProductBatch modellarida to'g'ridan-to'g'ri supplier_id
    yoki kirim partiyasi (batch/lot) havolasi mavjud emas. Shuning uchun ombordagi
    tovarlar yoki ko'p sotilganlar uchun faqat "eng oxirgi kirim ta'minotchisi"
    olinadi. O'tmishdagi sotuvlarga oxirgi ta'minotchini bog'lash taxminiy
    bo'lib, to'liq yechim uchun keyingi bosqichda Lot/Batch tracking yoki SaleItem
    ga supplier_id qo'shish talab etiladi.

    Ushbu funksiya ReportingFoundationService orqali N+1 siz, bitta samarali Subquery
    bilan ishlaydi.
    """
    res = ReportingFoundationService.get_latest_supplier_and_import_map(
        product_ids=product_ids,
        store_id=store_id,
        before=before,
    )
    return {pid: info.get("supplier") or "-" for pid, info in res.items()}


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


def _build_supplier_sales(params, store_id, user=None):
    """
    BILLZ "Yetkazib beruvchilar bo'yicha sotuvlar" formati:
    Authoritative StockLot + StockAllocation ma'lumotlar modeli asosida
    haqiqiy partiyalar, yetkazib beruvchilar, sof sotuv va tushum hisoboti.
    """
    from apps.reports.services.supplier_sales_report_service import SupplierSalesReportService
    return SupplierSalesReportService.build_report(params, store_id=store_id, user=user)



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
        {"key": "last_import", "label": "Oxirgi kirim", "kind": "text"},
        {"key": "purchase_price", "label": "Kelish narxi", "kind": "money"},
        {"key": "selling_price", "label": "Sotish narxi", "kind": "money"},
        {"key": "qty", "label": "Qoldiq", "kind": "int"},
        {"key": "purchase_value", "label": "Qoldiq (kelish)", "kind": "money"},
        {"key": "selling_value", "label": "Qoldiq (sotish)", "kind": "money"},
        {"key": "potential_profit", "label": "Kutilayotgan foyda", "kind": "money"},
        {"key": "margin_pct", "label": "Kutilayotgan marja", "kind": "text"},
    ]

    def build_row(b, qty, smap):
        p = b.product
        metrics = ReportingFoundationService.calculate_stock_leftovers_metrics(
            qty=qty,
            purchase_price=b.purchase_price,
            selling_price=b.selling_price,
        )
        sup_info = smap.get(b.product_id) or {}
        supplier_name = sup_info.get("supplier") or "-"
        last_import = sup_info.get("last_import") or "-"
        return {
            "store": b.store.name if b.store else "-",
            "name": p.name,
            "sku": p.sku or "-",
            "barcode": p.barcode or "-",
            "unit": p.unit_measurement.measurement if p.unit_measurement else "-",
            "category": p.category.name if p.category else "-",
            "brand": p.brand.name if p.brand else "-",
            "supplier": supplier_name,
            "last_import": last_import,
            "purchase_price": _money(metrics["purchase_price"]),
            "selling_price": _money(metrics["selling_price"]),
            "qty": metrics["qty"],
            "value": _money(metrics["purchase_value"]),
            "purchase_value": _money(metrics["purchase_value"]),
            "selling_value": _money(metrics["selling_value"]),
            "potential_profit": _money(metrics["potential_profit"]),
            "margin_pct": f"{metrics['margin_pct']}%",
        }

    # ── Joriy holat: queryset + row_fn (DB tomonda saralash/aggregat/pagination)
    if as_of is None:
        agg = qs.aggregate(
            n=Count("id"),
            qty=Coalesce(Sum("quantity"), Value(0), output_field=DecimalField()),
            purchase_val=Coalesce(
                Sum(ExpressionWrapper(
                    F("quantity") * F("purchase_price"), output_field=DecimalField(),
                )),
                Value(Decimal("0")), output_field=DecimalField(),
            ),
            selling_val=Coalesce(
                Sum(ExpressionWrapper(
                    F("quantity") * F("selling_price"), output_field=DecimalField(),
                )),
                Value(Decimal("0")), output_field=DecimalField(),
            ),
        )
        p_val = agg["purchase_val"]
        s_val = agg["selling_val"]
        pot_profit = s_val - p_val
        avg_margin = ((pot_profit / s_val) * 100) if s_val > 0 else Decimal("0")

        # Ta'minotchi va oxirgi kirim — bitta so'rovda barcha (filtrlangan) mahsulotlar uchun
        smap = ReportingFoundationService.get_latest_supplier_and_import_map(
            product_ids=qs.values("product_id"),
            store_id=store_id,
        )
        summary = [
            {"label": "Qatorlar (do'kon×mahsulot)", "value": agg["n"], "kind": "int"},
            {"label": "Jami qoldiq (dona)", "value": agg["qty"], "kind": "int"},
            {"label": "Qoldiq (kelish narxida)", "value": _money(p_val), "kind": "money"},
            {"label": "Qoldiq (sotish narxida)", "value": _money(s_val), "kind": "money"},
            {"label": "Kutilayotgan foyda", "value": _money(pot_profit), "kind": "money"},
            {"label": "Kutilayotgan marja", "value": f"{avg_margin:.1f}%", "kind": "text"},
        ]
        return columns, qs, lambda b: build_row(b, b.quantity or 0, smap), summary

    # ── O'tmish holati: sanadan keyingi harakatlar teskari qilinadi
    cutoff = day_end(as_of)
    delta = stock_delta_after(cutoff, store_id)
    batches = list(qs[:LARGE_EXPORT_CAP])
    smap = ReportingFoundationService.get_latest_supplier_and_import_map(
        product_ids={b.product_id for b in batches},
        store_id=store_id,
        before=cutoff,
    )

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
    total_purchase_val = sum(Decimal(r["purchase_value"]) for r in rows)
    total_selling_val = sum(Decimal(r["selling_value"]) for r in rows)
    total_potential_profit = sum(Decimal(r["potential_profit"]) for r in rows)
    avg_margin = ((total_potential_profit / total_selling_val) * 100) if total_selling_val > 0 else Decimal("0")

    summary = [
        {"label": "Qatorlar (do'kon×mahsulot)", "value": len(rows), "kind": "int"},
        {"label": "Jami qoldiq (dona)", "value": total_qty, "kind": "int"},
        {"label": "Qoldiq (kelish narxida)", "value": _money(total_purchase_val), "kind": "money"},
        {"label": "Qoldiq (sotish narxida)", "value": _money(total_selling_val), "kind": "money"},
        {"label": "Kutilayotgan foyda", "value": _money(total_potential_profit), "kind": "money"},
        {"label": "Kutilayotgan marja", "value": f"{avg_margin:.1f}%", "kind": "text"},
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
    "sales_by_product": {
        "label": "Tovarlar bo'yicha sotuvlar",
        "builder": _build_sales_by_product,
        "search": True,
        "export_cap": LARGE_EXPORT_CAP,
        "filters": lambda: [
            _f_daterange(),
            _f_store(),
            _f_category(),
            _f_brand(),
            _f_select("sort_by", "Saralash", [
                ("revenue", "Sof tushum bo'yicha"),
                ("quantity", "Sof miqdor bo'yicha"),
                ("profit", "Sof foyda bo'yicha"),
            ]),
        ],
    },
    "product_efficiency": {
        "label": "Tovarlar samaradorligi",
        "builder": _build_product_efficiency,
        "search": True,
        "export_cap": LARGE_EXPORT_CAP,
        "filters": lambda: [
            _f_daterange(),
            _f_store(),
            _f_category(),
            _f_brand(),
            _f_select("efficiency_status", "Samaradorlik holati", [
                ("active", "Faol"),
                ("dead_stock", "Harakatsiz"),
                ("out_of_stock", "Tugagan"),
                ("net_return", "Qaytarim ustun"),
            ], "Barchasi"),
            _f_select("sort_by", "Saralash", [
                ("revenue", "Sof tushum bo'yicha"),
                ("quantity", "Sof miqdor bo'yicha"),
                ("profit", "Sof foyda bo'yicha"),
                ("velocity", "Kunlik sotuv tezligi bo'yicha"),
                ("stock", "Joriy qoldiq bo'yicha"),
                ("doi", "DOI (qoldiq kunlari) bo'yicha"),
            ]),
        ],
    },
    "abc_analysis": {
        "label": "ABC tahlili",
        "builder": _build_abc_analysis,
        "search": True,
        "export_cap": LARGE_EXPORT_CAP,
        "filters": lambda: [
            _f_daterange(),
            _f_store(),
            _f_category(),
            _f_brand(),
            _f_select("metric", "Tahlil ko'rsatkichi", [
                ("revenue", "Sof tushum bo'yicha (sukut)"),
                ("profit", "Sof foyda bo'yicha"),
                ("quantity", "Sof miqdor bo'yicha"),
            ]),
            _f_select("abc_class", "ABC toifasi", [
                ("A", "A toifa (0% - 80%)"),
                ("B", "B toifa (80% - 95%)"),
                ("C", "C toifa (95% - 100%)"),
            ], "Barchasi"),
            _f_select("sort_by", "Saralash", [
                ("pareto", "Paretto tartibi (sukut)"),
                ("revenue", "Sof tushum bo'yicha"),
                ("profit", "Sof foyda bo'yicha"),
                ("quantity", "Sof miqdor bo'yicha"),
                ("share", "Ulush % bo'yicha"),
                ("cumulative", "Kumulyativ % bo'yicha"),
                ("name", "Tovar nomi bo'yicha"),
            ]),
        ],
    },
    "inventory_results": {
        "label": "Inventarizatsiya natijalari",
        "builder": _build_inventory_results,
        "search": True,
        "export_cap": LARGE_EXPORT_CAP,
        "filters": lambda: [
            _f_daterange(),
            _f_store(),
            _f_category(),
            _f_brand(),
            _f_select("status", "Holati", [
                ("matched", "Mos kelgan"),
                ("shortage", "Kamomad"),
                ("excess", "Ortiqcha"),
                ("unchecked", "Sanalmagan"),
            ], "Barchasi"),
            _f_select("sort_by", "Saralash", [
                ("session_id", "Sessiya ID bo'yicha (sukut)"),
                ("difference", "Tafovut miqdori bo'yicha"),
                ("shortage_qty", "Kamomad miqdori bo'yicha"),
                ("excess_qty", "Ortiqcha miqdori bo'yicha"),
                ("shortage_value", "Kamomad summasi bo'yicha"),
                ("excess_value", "Ortiqcha summasi bo'yicha"),
                ("expected_qty", "Kutilgan qoldiq bo'yicha"),
                ("counted_qty", "Sanalgan miqdor bo'yicha"),
                ("name", "Tovar nomi bo'yicha"),
                ("status", "Holat bo'yicha"),
            ]),
        ],
    },
    "order_returns": {
        "label": "Buyurtma qaytarishlari",
        "builder": _build_order_returns,
        "search": True,
        "export_cap": LARGE_EXPORT_CAP,
        "filters": lambda: [
            _f_daterange(),
            _f_store(),
            _f_supplier(),
            _f_category(),
            _f_brand(),
            _f_seller(),
            _f_text("order_id", "Buyurtma №"),
            _f_text("return_id", "Qaytarish №"),
            _f_text("sku", "SKU"),
            _f_text("barcode", "Shtrix-kod"),
            _f_select("sort_by", "Saralash", [
                ("return_datetime", "Qaytarish sanasi bo'yicha (sukut)"),
                ("returned_qty", "Qaytarilgan miqdor bo'yicha"),
                ("refund_amount", "Qaytarilgan summa bo'yicha"),
                ("profit_impact", "Yo'qotilgan foyda bo'yicha"),
                ("name", "Tovar nomi bo'yicha"),
                ("order_id", "Buyurtma № bo'yicha"),
                ("return_id", "Qaytarish № bo'yicha"),
            ]),
        ],
    },
    "write_offs": {
        "label": "Hisobdan chiqarishlar",
        "builder": _build_write_offs,
        "search": True,
        "export_cap": LARGE_EXPORT_CAP,
        "filters": lambda: [
            _f_daterange(),
            _f_store(),
            _f_select("reason", "Sabab", [
                ("damaged", "Buzilgan / yaroqsiz"),
                ("expired", "Muddati o'tgan"),
                ("lost", "Yo'qolgan / o'g'irlangan"),
                ("inventory", "Inventarizatsiya kamomadi"),
                ("catalog", "Katalogdan chiqarish"),
                ("other", "Boshqa"),
            ], empty_label="Barcha sabablar"),
            _f_supplier(),
            _f_category(),
            _f_brand(),
            _f_user("user_id", "Mas'ul xodim"),
            _f_text("write_off_id", "Hujjat №"),
            _f_text("sku", "SKU"),
            _f_text("barcode", "Shtrix-kod"),
            _f_select("sort_by", "Saralash", [
                ("write_off_datetime", "Sana bo'yicha (sukut)"),
                ("quantity", "Chiqarilgan miqdor bo'yicha"),
                ("purchase_value", "Tannarx summasi bo'yicha"),
                ("sale_value", "Sotuv summasi bo'yicha"),
                ("profit_impact", "Yo'qotilgan foyda bo'yicha"),
                ("name", "Tovar nomi bo'yicha"),
                ("write_off_id", "Hujjat № bo'yicha"),
            ]),
        ],
    },
    "imports": {
        "label": "Kirimlar (Importlar)",
        "builder": _build_imports,
        "search": True,
        "export_cap": LARGE_EXPORT_CAP,
        "filters": lambda: [
            _f_daterange(),
            _f_store(),
            _f_supplier(),
            _f_select("payment_status", "To'lov holati", [
                ("paid", "To'langan"),
                ("partial", "Qisman to'langan"),
                ("unpaid", "To'lanmagan"),
            ], empty_label="Barcha to'lov holatlari"),
            _f_user("user_id", "Mas'ul xodim"),
            _f_text("entry_id", "Hujjat №"),
            _f_text("sku", "SKU"),
            _f_text("barcode", "Shtrix-kod"),
            _f_category(),
            _f_brand(),
            _f_select("has_returns", "Qaytim holati", [
                ("true", "Faqat qaytarilgan tovarlar"),
                ("false", "Qaytimsiz tovarlar"),
            ], empty_label="Barchasi"),
            _f_select("sort_by", "Saralash", [
                ("entry_datetime", "Sana bo'yicha (sukut)"),
                ("entry_id", "Hujjat № bo'yicha"),
                ("quantity", "Qabul miqdori bo'yicha"),
                ("purchase_value", "Xarid summasi bo'yicha"),
                ("sale_value", "Sotuv summasi bo'yicha"),
                ("potential_margin", "Kutilayotgan foyda bo'yicha"),
                ("net_purchase_value", "Sof xarid summasi bo'yicha"),
                ("name", "Tovar nomi bo'yicha"),
                ("supplier_name", "Ta'minotchi bo'yicha"),
            ]),
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
        "builder_user": _build_supplier_sales,
        "search": True,
        # Katalog katta — standart 5k cap kesib qo'ymasin (to'liq eksport kerak)
        "export_cap": LARGE_EXPORT_CAP,
        "filters": lambda: [
            _f_daterange(), _f_store(), _f_supplier(), _f_product(), _f_category(), _f_brand(),
            _f_seller(),
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
