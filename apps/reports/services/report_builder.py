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
    Q, Sum, Value, When,
)
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.contract.models import StockEntryItem, Supplier, SupplierTransaction
from apps.debts.models import CustomerDebt
from apps.products.models import Category, Product, ProductBatch
from apps.products.services.product_query_service import (
    LOW_STOCK_THRESHOLD,
    annotate_stock_qty,
    apply_stock_status,
    apply_token_search,
)
from apps.sales.models import BankCard, Payment, Sale, SaleItem
from apps.store.models import Store
from apps.users.models.customers import Customer

from .report_service import _dt_bounds, _store_q, ExpensesService

# Eksportda ham cheklov bor — "hamma yozuvlar" hech qachon yuklanmaydi
EXPORT_MAX_ROWS = 5000
# Katta kataloglar (qoldiqlar, ta'minotchi sotuvlari) uchun kengaytirilgan cap —
# registry'da export_cap orqali tanlanadi
LARGE_EXPORT_CAP = 50_000
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

PAYMENT_TYPE_LABELS = {"cash": "Naqd", "card": "Karta", "mixed": "Aralash", "debt": "Qarz"}
SALE_STATUS_LABELS = {"paid": "To'langan", "partial": "Qisman", "debt": "Qarz", "r": "Qaytarilgan"}


# ─────────────────────────────────────────────
#  Param parsing yordamchilari
# ─────────────────────────────────────────────
def _parse_dates(params) -> tuple[date, date]:
    """from/to (ISO) — berilmasa oxirgi 30 kun."""
    today = timezone.localdate()
    from_raw, to_raw = params.get("from"), params.get("to")
    if from_raw and to_raw:
        try:
            return date.fromisoformat(from_raw), date.fromisoformat(to_raw)
        except ValueError:
            raise ValidationError({"from/to": "ISO format: YYYY-MM-DD"})
    return today - timedelta(days=30), today


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


def _f_select(param, label, pairs, empty_label=None):
    options = ([{"value": "", "label": empty_label}] if empty_label else []) + [
        {"value": v, "label": l} for v, l in pairs
    ]
    return {"param": param, "type": "select", "label": label, "options": options}


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
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)
    qs = (
        Sale.objects
        .filter(created_at__gte=start, created_at__lt=end)
        .filter(_store_q(store_id))
        .select_related("store", "customer", "seller")
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
            "payment": PAYMENT_TYPE_LABELS.get(s.payment_type, s.payment_type),
            "status": SALE_STATUS_LABELS.get(s.status, s.status),
        }

    summary = [
        {"label": "Sotuvlar soni", "value": agg["n"], "kind": "int"},
        {"label": "Jami summa", "value": _money(agg["total"]), "kind": "money"},
        {"label": "To'langan", "value": _money(agg["paid"]), "kind": "money"},
        {"label": "Qarz", "value": _money(agg["total"] - agg["paid"]), "kind": "money"},
    ]
    return columns, qs, row, summary


def _build_top_products(params, store_id):
    d_from, d_to = _parse_dates(params)
    start, end = _dt_bounds(d_from, d_to)
    top_n = _parse_int(params, "top", 20, allowed={10, 20, 50, 100})
    sort_by = params.get("sort_by") if params.get("sort_by") in ("quantity", "revenue") else "revenue"

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
            sold_qty=Coalesce(
                Sum(ExpressionWrapper(
                    F("quantity") - F("returned_quantity"), output_field=IntegerField(),
                )),
                Value(0), output_field=IntegerField(),
            ),
            revenue_sum=Coalesce(
                Sum(ExpressionWrapper(
                    F("unit_price") * (F("quantity") - F("returned_quantity")),
                    output_field=DecimalField(),
                )),
                Value(Decimal("0")), output_field=DecimalField(),
            ),
        )
        .filter(sold_qty__gt=0)
        .order_by("-sold_qty" if sort_by == "quantity" else "-revenue_sum")[:top_n]
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
        }
        for i, r in enumerate(rows_raw)
    ]
    summary = [
        {"label": "Mahsulotlar", "value": len(rows), "kind": "int"},
        {"label": "Jami sotilgan", "value": sum(r["quantity"] for r in rows), "kind": "int"},
        {"label": "Jami daromad", "value": _money(sum(Decimal(r["revenue"]) for r in rows)), "kind": "money"},
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
        stock=Coalesce(Sum("stock_qty"), Value(0), output_field=IntegerField()),
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


def _last_supplier_map(product_ids) -> dict:
    """
    product_id → oxirgi kirim (StockEntry) ta'minotchisi nomi.
    Mahsulotda to'g'ridan-to'g'ri supplier maydoni yo'q — bog'lanish kirim
    tarixidan olinadi: id bo'yicha o'sish tartibida yozib borilganda oxirgi
    kirim ta'minotchisi dictda qoladi.
    """
    smap = {}
    items = (
        StockEntryItem.objects
        .filter(product_id__in=product_ids)
        .order_by("id")
        .values("product_id", "entry__supplier__name")
    )
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
            sold_qty=Coalesce(Sum("quantity"), Value(0), output_field=IntegerField()),
            returned_qty=Coalesce(Sum("returned_quantity"), Value(0), output_field=IntegerField()),
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
    joriy qoldiq — o'lchov birligi, toifa, brend, ta'minotchi va narxlar bilan.
    """
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

    agg = qs.aggregate(
        n=Count("id"),
        qty=Coalesce(Sum("quantity"), Value(0), output_field=IntegerField()),
        value=Coalesce(
            Sum(ExpressionWrapper(
                F("quantity") * F("purchase_price"), output_field=DecimalField(),
            )),
            Value(Decimal("0")), output_field=DecimalField(),
        ),
    )
    # Ta'minotchi — bitta so'rovda barcha (filtrlangan) mahsulotlar uchun
    smap = _last_supplier_map(qs.values("product_id"))

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

    def row(b):
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
            "qty": b.quantity,
            "value": _money((b.quantity or 0) * (b.purchase_price or 0)),
        }

    summary = [
        {"label": "Qatorlar (do'kon×mahsulot)", "value": agg["n"], "kind": "int"},
        {"label": "Jami qoldiq (dona)", "value": agg["qty"], "kind": "int"},
        {"label": "Qoldiq summasi (kelish narxida)", "value": _money(agg["value"]), "kind": "money"},
    ]
    return columns, qs, row, summary


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
            _f_select("sort_by", "Saralash", [("revenue", "Daromad bo'yicha"), ("quantity", "Miqdor bo'yicha")]),
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
    def _run(params) -> tuple[list, list, list]:
        """Builder ishga tushirib to'liq (columns, rows, summary) qaytaradi — cap bilan."""
        report_type = params.get("report_type")
        spec = REPORTS.get(report_type)
        if not spec:
            raise ValidationError({"report_type": "Noma'lum hisobot turi"})
        store_id = _parse_store(params)
        columns, rows_or_qs, row_fn, summary = spec["builder"](params, store_id)
        return columns, rows_or_qs, row_fn, summary

    @staticmethod
    def generate(params) -> dict:
        columns, rows_or_qs, row_fn, summary = ReportBuilderService._run(params)
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

        return {
            "columns": columns,
            "rows": rows,
            "summary": summary,
            "total": total,
            "page": page,
            "limit": limit,
        }

    @staticmethod
    def export_rows(params) -> tuple[str, list, list, list]:
        """Eksport uchun: (label, columns, BARCHA qatorlar[cap], summary) — generate bilan bir xil filtrlar."""
        report_type = params.get("report_type")
        spec = REPORTS.get(report_type)
        if not spec:
            raise ValidationError({"report_type": "Noma'lum hisobot turi"})
        columns, rows_or_qs, row_fn, summary = ReportBuilderService._run(params)
        cap = spec.get("export_cap", EXPORT_MAX_ROWS)
        if row_fn is None:
            rows = list(rows_or_qs)[:cap]
        else:
            rows = [row_fn(obj) for obj in rows_or_qs[:cap]]
        return spec["label"], columns, rows, summary
