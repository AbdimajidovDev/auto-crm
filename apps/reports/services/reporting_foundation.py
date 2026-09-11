"""
Reporting Foundation — Barcha hisobotlar uchun yagona hisob-kitob va aggregatsiya qatlami.

Ushbu modul hisobotlar modulidagi barcha moliyaviy va tovar ko'rsatkichlarining
yagona manbai (source-of-truth) bo'lib xizmat qiladi:
  1. Sotuvlar va qaytarimlar (Sales & Returns foundation):
     - gross_sold_qty, returned_qty, net_sold_qty
     - gross_revenue, return_amount, net_revenue
     - purchase_cost, discount, net_profit, margin_pct
  2. Davriy qaytarimlar mantig'i (Period return logic):
     - Agar tovar bir davrda sotilib, boshqa davrda qaytarilsa, qaytarim aynan
       o'zining amalga oshirilgan sanasi (SaleReturn.created_at) bo'yicha davrga kiradi.
     - O'tgan davrning yopilgan hisoboti buzilmaydi.
  3. Qoldiqlar ko'rsatkichlari (Stock leftovers metrics):
     - purchase_value, selling_value, potential_profit, margin_pct, last_import
  4. Yetkazib beruvchini aniqlash (Supplier resolution):
     - Ma'lumotlar modeli cheklovi: SaleItem va ProductBatch modellarida to'g'ridan-to'g'ri
       supplier_id yoki batch/lot havolasi yo'q. Shuning uchun ombordagi qoldiqlar
       uchun faqat "oxirgi kirim ta'minotchisi" (latest supplier before as_of) olinadi.
       Taxminiy/soxta mapping qilinmaydi va bu cheklov to'liq hujjatlashtirilgan.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from django.db.models import (
    Case,
    Count,
    DecimalField,
    Exists,
    ExpressionWrapper,
    F,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from apps.contract.models import (
    StockEntry,
    StockEntryItem,
    StockEntryReturnItem,
    SupplierTransaction,
)
from apps.inventory.models import (
    InventoryCount,
    InventoryMovement,
    InventorySession,
    InventorySnapshot,
)
from apps.products.models import Product, ProductBatch
from apps.sales.models import Payment, Sale, SaleItem, SaleReturn, SaleReturnItem
from apps.sales.profit import sum_item_profit
from apps.writeoff.models import WriteOff, WriteOffItem

EFFICIENCY_STATUS_LABELS = {
    "active": "Faol",
    "dead_stock": "Harakatsiz",
    "out_of_stock": "Tugagan",
    "net_return": "Qaytarim ustun",
}

MONEY_FIELD = DecimalField(max_digits=20, decimal_places=2)
QTY_FIELD = DecimalField(max_digits=12, decimal_places=2)
ZERO_MONEY = Value(Decimal("0.00"), output_field=MONEY_FIELD)
ZERO_QTY = Value(Decimal("0.00"), output_field=QTY_FIELD)


@dataclass(frozen=True)
class PeriodSalesMetrics:
    """
    Muayyan davr uchun savdo va qaytarim ko'rsatkichlari agregatsiyasi.
    """
    gross_sold_qty: Decimal
    returned_qty: Decimal
    net_sold_qty: Decimal
    gross_revenue: Decimal
    return_amount: Decimal
    total_discount: Decimal
    net_revenue: Decimal
    gross_purchase_cost: Decimal
    returned_purchase_cost: Decimal
    net_purchase_cost: Decimal
    net_profit: Decimal
    margin_pct: Decimal
    markup_pct: Decimal
    sales_count: int         # Qaytarilmagan faol cheklar soni
    all_sales_count: int     # Jami cheklar soni (qaytarilganlar bilan)
    returns_count: int       # Davrdagi qaytarim operatsiyalari soni


class ReportingFoundationService:
    """
    Hisobotlar moduli uchun markaziy aggregatsiya va formulalar xizmati.
    """

    # ─────────────────────────────────────────────────────────────
    # 1. Marja va Ustama (Margin & Markup) hisob-kitoblari
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def calculate_margin_and_markup(
        revenue: Decimal | float | int | None,
        cost: Decimal | float | int | None,
    ) -> dict[str, Decimal]:
        """
        Margin va Markup ko'rsatkichlarini hisoblaydi:
          Margin = ((Revenue - Cost) / Revenue) * 100 (Daromaddan olingan foyda ulushi)
          Markup = ((Revenue - Cost) / Cost) * 100 (Tannarx ustiga qo'yilgan ustama ulushi)
        ZeroDivision holatlarini xavfsiz qaytaradi.
        """
        rev = Decimal(str(revenue or 0))
        cst = Decimal(str(cost or 0))
        profit = rev - cst

        if rev > Decimal("0"):
            margin_pct = ((profit / rev) * Decimal("100")).quantize(Decimal("0.1"))
        else:
            margin_pct = Decimal("0.0")

        if cst > Decimal("0"):
            markup_pct = ((profit / cst) * Decimal("100")).quantize(Decimal("0.1"))
        else:
            markup_pct = Decimal("0.0")

        return {
            "revenue": rev,
            "cost": cst,
            "profit": profit,
            "margin_pct": margin_pct,
            "markup_pct": markup_pct,
        }

    # ─────────────────────────────────────────────────────────────
    # 2. Qoldiqlar (Stock leftovers) hisob-kitoblari
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def calculate_stock_leftovers_metrics(
        qty: Decimal | float | int | None,
        purchase_price: Decimal | float | int | None,
        selling_price: Decimal | float | int | None,
    ) -> dict[str, Decimal]:
        """
        Qoldiq qatori uchun barcha moliyaviy metrikalarni hisoblaydi.

        Formulalar:
          - purchase_value = qty * purchase_price
          - selling_value  = qty * selling_price
          - potential_profit = (selling_price - purchase_price) * qty
          - margin_pct = ((selling_price - purchase_price) / selling_price) * 100
          - markup_pct = ((selling_price - purchase_price) / purchase_price) * 100
        """
        q = Decimal(str(qty or 0))
        p_cost = Decimal(str(purchase_price or 0))
        p_sell = Decimal(str(selling_price or 0))

        purchase_value = (q * p_cost).quantize(Decimal("0.01"))
        selling_value = (q * p_sell).quantize(Decimal("0.01"))
        potential_profit = ((p_sell - p_cost) * q).quantize(Decimal("0.01"))

        mm = ReportingFoundationService.calculate_margin_and_markup(
            revenue=p_sell,
            cost=p_cost,
        )

        return {
            "qty": q,
            "purchase_price": p_cost,
            "selling_price": p_sell,
            "purchase_value": purchase_value,
            "selling_value": selling_value,
            "potential_profit": potential_profit,
            "margin_pct": mm["margin_pct"],
            "markup_pct": mm["markup_pct"],
        }

    @staticmethod
    def get_latest_supplier_and_import_map(
        product_ids: Iterable[int],
        store_id: int | None = None,
        before: datetime | None = None,
    ) -> dict[int, dict[str, str | None]]:
        """
        Tovar ID'lari bo'yicha eng oxirgi kirim ta'minotchisi va kirim sanasini
        N+1 va xotira yuklanishisiz (Subquery orqali) samarali aniqlaydi.

        Qaytaradi: {product_id: {"supplier": "Nom", "last_import": "YYYY-MM-DD HH:MM"}}
        """
        ids: set[int] = set()
        if hasattr(product_ids, "values_list"):
            try:
                ids = set(product_ids.values_list("product_id", flat=True))
            except Exception:
                try:
                    ids = set(product_ids.values_list("pk", flat=True))
                except Exception:
                    ids = set()
        else:
            for item in product_ids:
                if isinstance(item, dict):
                    val = item.get("product_id") or item.get("id") or item.get("pk")
                    if val is not None:
                        ids.add(val)
                elif item is not None:
                    ids.add(item)

        if not ids:
            return {}

        base_filter = Q(product_id__in=ids)
        if before is not None:
            base_filter &= Q(entry__created_at__lt=before)
        if store_id is not None:
            base_filter &= Q(entry__store_id=store_id)

        # Har bir mahsulot uchun eng oxirgi StockEntryItem ID sini topamiz
        latest_ids_qs = (
            StockEntryItem.objects
            .filter(base_filter)
            .values("product_id")
            .annotate(max_id=Max("id"))
            .values("max_id")
        )

        items = (
            StockEntryItem.objects
            .filter(id__in=Subquery(latest_ids_qs))
            .values("product_id", "entry__supplier__name", "entry__created_at")
        )

        result: dict[int, dict[str, str | None]] = {}
        for it in items:
            dt = it.get("entry__created_at")
            result[it["product_id"]] = {
                "supplier": it.get("entry__supplier__name") or "-",
                "last_import": dt.strftime("%Y-%m-%d %H:%M") if dt else "-",
            }

        # Agar store_id bo'yicha topilmagan mahsulotlar bo'lsa, umumiy bazadan fallback qilamiz
        missing_ids = ids - set(result.keys())
        if missing_ids and store_id is not None:
            fallback_map = ReportingFoundationService.get_latest_supplier_and_import_map(
                product_ids=missing_ids,
                store_id=None,
                before=before,
            )
            result.update(fallback_map)

        return result

    # ─────────────────────────────────────────────────────────────
    # 2. Cheklar ro'yxati (Sale queryset) uchun Net annotatsiyalar
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def annotate_sale_net_fields(qs: QuerySet[Sale]) -> QuerySet[Sale]:
        """
        Sale querysetiga qaytarimlarni inobatga oluvchi sof maydonlarni bog'laydi:
          - refunded_total: Chek bo'yicha qaytarilgan jami summa (SaleReturn orqali)
          - net_total: Haqiqiy sof tushum (to'liq qaytarilgan bo'lsa 0, qisman bo'lsa total - refunded)
          - refunded_payments: Mijozga pul shaklida qaytarilgan to'lovlar summasi (Payment.is_refund)
          - net_paid: Haqiqiy to'langan summa (paid_amount - refunded_payments)
          - net_debt: Haqiqiy qolgan qarz (max(0, net_total - net_paid))
          - net_profit: Sof foyda (sum_item_profit orqali chegirmalar va qaytarilgan donalar chegirilgan)
        """
        # Chek bo'yicha barcha qaytarimlar (SaleReturn) summasi
        returns_sq = (
            SaleReturn.objects
            .filter(sale=OuterRef("pk"))
            .values("sale")
            .annotate(total=Coalesce(Sum("total_refund"), ZERO_MONEY))
            .values("total")[:1]
        )

        # Chek bo'yicha mijozga pul shaklida qaytarilgan to'lovlar
        refund_payments_sq = (
            Payment.objects
            .filter(sale=OuterRef("pk"), is_refund=True)
            .values("sale")
            .annotate(total=Coalesce(Sum("amount"), ZERO_MONEY))
            .values("total")[:1]
        )

        # Har chek uchun sof foyda
        profit_sq = (
            SaleItem.objects
            .filter(sale=OuterRef("pk"))
            .values("sale")
            .annotate(total=sum_item_profit())
            .values("total")[:1]
        )

        return (
            qs
            .annotate(
                _refunded_total=Coalesce(Subquery(returns_sq, output_field=MONEY_FIELD), ZERO_MONEY),
                _refunded_paid=Coalesce(Subquery(refund_payments_sq, output_field=MONEY_FIELD), ZERO_MONEY),
                net_profit=Coalesce(Subquery(profit_sq, output_field=MONEY_FIELD), ZERO_MONEY),
            )
            .annotate(
                # To'liq qaytarilgan bo'lsa (status='r') sof summa = 0
                net_total=Case(
                    When(status=Sale.Status.RETURNED, then=ZERO_MONEY),
                    When(total_amount__gt=F("_refunded_total"), then=F("total_amount") - F("_refunded_total")),
                    default=ZERO_MONEY,
                    output_field=MONEY_FIELD,
                ),
                net_paid=Case(
                    When(status=Sale.Status.RETURNED, then=ZERO_MONEY),
                    When(paid_amount__gt=F("_refunded_paid"), then=F("paid_amount") - F("_refunded_paid")),
                    default=ZERO_MONEY,
                    output_field=MONEY_FIELD,
                ),
            )
            .annotate(
                net_debt=Case(
                    When(status=Sale.Status.RETURNED, then=ZERO_MONEY),
                    When(net_total__gt=F("net_paid"), then=F("net_total") - F("net_paid")),
                    default=ZERO_MONEY,
                    output_field=MONEY_FIELD,
                ),
            )
        )

    # ─────────────────────────────────────────────────────────────
    # 3. Davriy savdo va qaytarim ko'rsatkichlari (Period Metrics)
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_period_sales_metrics(
        start: datetime,
        end: datetime,
        store_id: int | None = None,
    ) -> PeriodSalesMetrics:
        """
        Davr ichidagi savdolar va davr ichidagi qaytarimlarni birlashtirib,
        to'g'ri transactional balansni hisoblaydi.

        Period Return Logic:
          - Davrda sotilgan tovarlar: Sale.created_at in [start, end)
          - Davrda qaytarilgan tovarlar: SaleReturn.created_at in [start, end)
          - Bu o'tgan oydagi sotuv bu oyda qaytarilganda, joriy oy tushumidan
            ayirilishini va o'tgan oy hisoboti buzilmasligini kafolatlaydi.
        """
        # 1. Davrdagi sotuvlar
        sales_filter = Q(created_at__gte=start, created_at__lt=end)
        if store_id:
            sales_filter &= Q(store_id=store_id)

        sales_qs = Sale.objects.filter(sales_filter)
        active_sales_qs = sales_qs.exclude(status=Sale.Status.RETURNED)

        # Sotuv qatorlari (faqat faol sotuvlar)
        sale_items = SaleItem.objects.filter(sale__in=active_sales_qs)
        sales_agg = sale_items.aggregate(
            gross_qty=Coalesce(Sum("quantity"), ZERO_QTY),
            gross_rev=Coalesce(Sum("total_price"), ZERO_MONEY),
            gross_cost=Coalesce(
                Sum(ExpressionWrapper(F("quantity") * Coalesce(F("purchase_price"), ZERO_MONEY), output_field=MONEY_FIELD)),
                ZERO_MONEY,
            ),
        )

        sale_counts = sales_qs.aggregate(
            all_n=Count("id"),
            active_n=Count("id", filter=~Q(status=Sale.Status.RETURNED)),
            total_discount=Coalesce(
                Sum("discount_amount", filter=~Q(status=Sale.Status.RETURNED)),
                ZERO_MONEY,
            ),
        )

        # 2. Davrdagi qaytarimlar (AYNAN shu davrda rasmiylashtirilgan SaleReturn)
        returns_filter = Q(created_at__gte=start, created_at__lt=end)
        if store_id:
            returns_filter &= Q(store_id=store_id)

        returns_qs = SaleReturn.objects.filter(returns_filter)
        return_items = SaleReturnItem.objects.filter(sale_return__in=returns_qs)

        returns_agg = return_items.aggregate(
            ret_qty=Coalesce(Sum("quantity"), ZERO_QTY),
            ret_amount=Coalesce(Sum("total_price"), ZERO_MONEY),
            ret_cost=Coalesce(
                Sum(ExpressionWrapper(
                    F("quantity") * Coalesce(F("sale_item__purchase_price"), ZERO_MONEY),
                    output_field=MONEY_FIELD,
                )),
                ZERO_MONEY,
            ),
        )
        returns_count = returns_qs.count()

        # 3. Yig'ma ko'rsatkichlar
        gross_sold_qty = Decimal(str(sales_agg["gross_qty"] or 0))
        returned_qty = Decimal(str(returns_agg["ret_qty"] or 0))
        net_sold_qty = max(Decimal("0.00"), gross_sold_qty - returned_qty)

        gross_revenue = Decimal(str(sales_agg["gross_rev"] or 0))
        total_discount = Decimal(str(sale_counts["total_discount"] or 0))
        return_amount = Decimal(str(returns_agg["ret_amount"] or 0))

        # Chegirma chegirilgan sotuv summasi
        revenue_after_discount = max(Decimal("0.00"), gross_revenue - total_discount)
        # Sof tushum = Chegirmali sotuv - Davrdagi qaytarimlar
        net_revenue = max(Decimal("0.00"), revenue_after_discount - return_amount)

        gross_cost = Decimal(str(sales_agg["gross_cost"] or 0))
        returned_cost = Decimal(str(returns_agg["ret_cost"] or 0))
        net_purchase_cost = max(Decimal("0.00"), gross_cost - returned_cost)

        # Sof foyda = Sof tushum - Sof tannarx
        net_profit = net_revenue - net_purchase_cost

        mm = ReportingFoundationService.calculate_margin_and_markup(
            revenue=net_revenue,
            cost=net_purchase_cost,
        )

        return PeriodSalesMetrics(
            gross_sold_qty=gross_sold_qty,
            returned_qty=returned_qty,
            net_sold_qty=net_sold_qty,
            gross_revenue=gross_revenue,
            return_amount=return_amount,
            total_discount=total_discount,
            net_revenue=net_revenue,
            gross_purchase_cost=gross_cost,
            returned_purchase_cost=returned_cost,
            net_purchase_cost=net_purchase_cost,
            net_profit=net_profit,
            margin_pct=mm["margin_pct"],
            markup_pct=mm["markup_pct"],
            sales_count=sale_counts["active_n"],
            all_sales_count=sale_counts["all_n"],
            returns_count=returns_count,
        )

    # ─────────────────────────────────────────────────────────────
    # 4. Tovarlar bo'yicha sotuvlar (Sales by Product)
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_sales_by_product_metrics(
        start: datetime,
        end: datetime,
        store_id: int | None = None,
        category_id: int | None = None,
        brand_id: int | None = None,
        product_id: int | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        seller_id: int | None = None,
        customer_id: int | None = None,
        search: str | None = None,
        sort_by: str = "revenue",
    ) -> tuple[list[dict], dict]:
        """
        Tovarlar bo'yicha sotuvlar (Sales by Product) hisob-kitoblarini
        PostgreSQL darajasida N+1 siz, to'liq aggregatsiyalaydi.

        Period Return Logic:
          - Sotilgan tovarlar: SaleItem (sale.created_at in [start, end))
          - Qaytarilgan tovarlar: SaleReturnItem (sale_return.created_at in [start, end))
          - Net sold qty = sold_qty - returned_qty
          - Gross sales = Sum(SaleItem.total_price)
          - Discount = Proportional item discount share
          - Return amount = Sum(SaleReturnItem.total_price)
          - Net revenue = (Gross sales - Discount) - Return amount
          - Total cost = Gross cost - Returned cost
          - Profit = Net revenue - Total cost
          - Margin % = (Profit / Net revenue) * 100
        """
        sales_filter = Q(
            sale__created_at__gte=start,
            sale__created_at__lt=end,
            sale__deleted_at__isnull=True,
        )
        if store_id:
            sales_filter &= Q(sale__store_id=store_id)
        if category_id:
            sales_filter &= Q(product__category_id=category_id)
        if brand_id:
            sales_filter &= Q(product__brand_id=brand_id)
        if product_id:
            sales_filter &= Q(product_id=product_id)
        if sku and sku.strip():
            sales_filter &= Q(product__sku__iexact=sku.strip())
        if barcode and barcode.strip():
            sales_filter &= Q(product__barcode__iexact=barcode.strip())
        if seller_id:
            sales_filter &= Q(sale__seller_id=seller_id)
        if customer_id:
            sales_filter &= Q(sale__customer_id=customer_id)
        if search and search.strip():
            s = search.strip()
            sales_filter &= (
                Q(product__name__icontains=s)
                | Q(product__sku__icontains=s)
                | Q(product__barcode__icontains=s)
            )

        # Chek darajasidagi chegirmani qatorga proporsional taqsimlash
        subtotal_expr = ExpressionWrapper(
            Coalesce(F("sale__total_amount"), ZERO_MONEY) + Coalesce(F("sale__discount_amount"), ZERO_MONEY),
            output_field=MONEY_FIELD,
        )
        line_rev_expr = ExpressionWrapper(
            F("unit_price") * F("quantity"),
            output_field=MONEY_FIELD,
        )
        item_discount_expr = Case(
            When(
                sale__discount_amount__gt=0,
                then=ExpressionWrapper(
                    Coalesce(F("sale__discount_amount"), ZERO_MONEY) * line_rev_expr / subtotal_expr,
                    output_field=MONEY_FIELD,
                ),
            ),
            default=ZERO_MONEY,
            output_field=MONEY_FIELD,
        )
        line_cost_expr = ExpressionWrapper(
            F("quantity") * Coalesce(F("purchase_price"), ZERO_MONEY),
            output_field=MONEY_FIELD,
        )

        group_fields = [
            "sale__store_id",
            "sale__store__name",
            "product_id",
            "product__name",
            "product__sku",
            "product__barcode",
            "product__category__name",
            "product__brand__name",
            "product__unit_measurement__measurement",
        ]

        sales_data = list(
            SaleItem.objects
            .filter(sales_filter)
            .values(*group_fields)
            .annotate(
                sold_qty=Coalesce(Sum("quantity"), ZERO_QTY),
                gross_sales=Coalesce(Sum("total_price"), ZERO_MONEY),
                discount=Coalesce(Sum(item_discount_expr), ZERO_MONEY),
                gross_cost=Coalesce(Sum(line_cost_expr), ZERO_MONEY),
                missing_cost_count=Count("id", filter=Q(purchase_price__isnull=True) | Q(purchase_price=0)),
            )
        )

        # Davrdagi qaytarimlar (aynan shu davrda rasmiylashtirilgan)
        returns_filter = Q(
            sale_return__created_at__gte=start,
            sale_return__created_at__lt=end,
            sale_return__sale__deleted_at__isnull=True,
        )
        if store_id:
            returns_filter &= Q(sale_return__store_id=store_id)
        if category_id:
            returns_filter &= Q(product__category_id=category_id)
        if brand_id:
            returns_filter &= Q(product__brand_id=brand_id)
        if product_id:
            returns_filter &= Q(product_id=product_id)
        if sku and sku.strip():
            returns_filter &= Q(product__sku__iexact=sku.strip())
        if barcode and barcode.strip():
            returns_filter &= Q(product__barcode__iexact=barcode.strip())
        if seller_id:
            returns_filter &= (
                Q(sale_return__seller_id=seller_id) | Q(sale_return__sale__seller_id=seller_id)
            )
        if customer_id:
            returns_filter &= (
                Q(sale_return__customer_id=customer_id) | Q(sale_return__sale__customer_id=customer_id)
            )
        if search and search.strip():
            s = search.strip()
            returns_filter &= (
                Q(product__name__icontains=s)
                | Q(product__sku__icontains=s)
                | Q(product__barcode__icontains=s)
            )

        ret_cost_expr = ExpressionWrapper(
            F("quantity") * Coalesce(F("sale_item__purchase_price"), ZERO_MONEY),
            output_field=MONEY_FIELD,
        )

        ret_group_fields = [
            "sale_return__store_id",
            "sale_return__store__name",
            "product_id",
            "product__name",
            "product__sku",
            "product__barcode",
            "product__category__name",
            "product__brand__name",
            "product__unit_measurement__measurement",
        ]

        returns_data = list(
            SaleReturnItem.objects
            .filter(returns_filter)
            .values(*ret_group_fields)
            .annotate(
                ret_qty=Coalesce(Sum("quantity"), ZERO_QTY),
                ret_amount=Coalesce(Sum("total_price"), ZERO_MONEY),
                ret_cost=Coalesce(Sum(ret_cost_expr), ZERO_MONEY),
            )
        )

        sales_map: dict[tuple[int, int], dict] = {
            (r["sale__store_id"], r["product_id"]): r for r in sales_data
        }
        returns_map: dict[tuple[int, int], dict] = {
            (r["sale_return__store_id"], r["product_id"]): r for r in returns_data
        }

        all_keys = sorted(
            list(set(sales_map.keys()) | set(returns_map.keys())),
            key=lambda k: (k[0], k[1]),
        )

        rows = []
        any_missing_cost = False

        total_sold_qty = Decimal("0.00")
        total_ret_qty = Decimal("0.00")
        total_net_sold_qty = Decimal("0.00")
        total_gross_sales = Decimal("0.00")
        total_discount = Decimal("0.00")
        total_net_revenue = Decimal("0.00")
        total_cost = Decimal("0.00")
        total_profit = Decimal("0.00")

        for key in all_keys:
            s = sales_map.get(key)
            r = returns_map.get(key)

            meta_src = s or r
            store_name = (s["sale__store__name"] if s else r["sale_return__store__name"]) or "-"
            p_id = meta_src["product_id"]
            p_name = meta_src["product__name"] or "-"
            sku_val = meta_src["product__sku"] or "-"
            barcode_val = meta_src["product__barcode"] or "-"
            cat_name = meta_src["product__category__name"] or "-"
            b_name = meta_src["product__brand__name"] or "-"
            unit_val = meta_src["product__unit_measurement__measurement"] or "-"

            sold_qty = Decimal(str(s["sold_qty"] if s else 0))
            ret_qty = Decimal(str(r["ret_qty"] if r else 0))
            net_sold_qty = sold_qty - ret_qty

            gross_sales = Decimal(str(s["gross_sales"] if s else 0))
            discount = Decimal(str(s["discount"] if s else 0)).quantize(Decimal("0.01"))
            ret_amount = Decimal(str(r["ret_amount"] if r else 0))
            net_revenue = (gross_sales - discount - ret_amount).quantize(Decimal("0.01"))

            gross_cost = Decimal(str(s["gross_cost"] if s else 0))
            ret_cost = Decimal(str(r["ret_cost"] if r else 0))
            cost = (gross_cost - ret_cost).quantize(Decimal("0.01"))

            profit = (net_revenue - cost).quantize(Decimal("0.01"))

            if net_revenue > Decimal("0"):
                margin_pct = ((profit / net_revenue) * Decimal("100")).quantize(Decimal("0.1"))
            else:
                margin_pct = Decimal("0.0")

            unit_cost = (gross_cost / sold_qty).quantize(Decimal("0.01")) if sold_qty > 0 else Decimal("0.00")

            has_missing = bool(s and s.get("missing_cost_count", 0) > 0)
            if has_missing:
                any_missing_cost = True

            row = {
                "store_id": key[0],
                "store": store_name,
                "product_id": p_id,
                "name": p_name,
                "sku": sku_val,
                "barcode": barcode_val,
                "category": cat_name,
                "brand": b_name,
                "unit": unit_val,
                "sold_qty": sold_qty,
                "returned_qty": ret_qty,
                "net_sold_qty": net_sold_qty,
                "gross_sales": gross_sales,
                "discount": discount,
                "net_revenue": net_revenue,
                "free_price": "-",
                "unit_cost": unit_cost,
                "total_cost": cost,
                "profit": profit,
                "margin_pct": margin_pct,
                "has_missing_cost": has_missing,
            }
            rows.append(row)

            total_sold_qty += sold_qty
            total_ret_qty += ret_qty
            total_net_sold_qty += net_sold_qty
            total_gross_sales += gross_sales
            total_discount += discount
            total_net_revenue += net_revenue
            total_cost += cost
            total_profit += profit

        # Saralash
        if sort_by == "quantity":
            rows.sort(key=lambda x: (-x["sold_qty"], -x["net_revenue"]))
        elif sort_by == "profit":
            rows.sort(key=lambda x: (-x["profit"], -x["net_revenue"]))
        else:  # revenue default
            rows.sort(key=lambda x: (-x["net_revenue"], -x["sold_qty"]))

        overall_margin = (
            ((total_profit / total_net_revenue) * Decimal("100")).quantize(Decimal("0.1"))
            if total_net_revenue > Decimal("0")
            else Decimal("0.0")
        )

        totals = {
            "count": len(rows),
            "total_sold_qty": total_sold_qty,
            "total_ret_qty": total_ret_qty,
            "total_net_sold_qty": total_net_sold_qty,
            "total_gross_sales": total_gross_sales,
            "total_discount": total_discount,
            "total_net_revenue": total_net_revenue,
            "total_cost": total_cost,
            "total_profit": total_profit,
            "overall_margin": overall_margin,
            "any_missing_cost": any_missing_cost,
        }

        return rows, totals

    # ─────────────────────────────────────────────────────────────
    # 5. Tovarlar samaradorligi (Product Efficiency)
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_product_efficiency_metrics(
        start: datetime,
        end: datetime,
        store_id: int | None = None,
        category_id: int | None = None,
        brand_id: int | None = None,
        product_id: int | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        seller_id: int | None = None,
        customer_id: int | None = None,
        efficiency_status: str | None = None,
        search: str | None = None,
        sort_by: str = "revenue",
    ) -> tuple[list[dict], dict]:
        """
        Tovarlar samaradorligi (Product Efficiency) hisob-kitoblari:
        Sotuvlar davri (Sales & Returns) va joriy qoldiqlar (ProductBatch) o'rtasidagi
        gibrid birlashma (hybrid union).

        Asosiy ko'rsatkichlar:
          - net_sold_qty: Davrdagi sof sotilgan miqdor
          - current_stock: Joriy ombor qoldig'i
          - sales_velocity: Kunlik o'rtacha sotuv tezligi (net_sold_qty / period_days)
          - doi: Days of Inventory — qoldiq necha kunga yetishi (current_stock / sales_velocity)
          - revenue_share_pct: Tovarning davr tushumidagi ulushi %
          - efficiency_status: 'optimal' | 'fast_moving' | 'overstocked' | 'dead_stock' | 'out_of_stock' | 'net_return'
        """
        # 1. Sotuvlar va qaytarimlar bazasi (mavjud foundation qayta ishlatiladi)
        sales_rows, _ = ReportingFoundationService.get_sales_by_product_metrics(
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
            sort_by="revenue",
        )

        sales_map: dict[tuple[int, int], dict] = {
            (r["store_id"], r["product_id"]): r for r in sales_rows
        }

        # 2. Joriy qoldiqlar (ProductBatch) filtri
        batch_filter = Q(is_active=True, product__status=Product.ProductStatus.ACTIVE)
        if store_id:
            batch_filter &= Q(store_id=store_id)
        if category_id:
            batch_filter &= Q(product__category_id=category_id)
        if brand_id:
            batch_filter &= Q(product__brand_id=brand_id)
        if product_id:
            batch_filter &= Q(product_id=product_id)
        if sku and sku.strip():
            batch_filter &= Q(product__sku__iexact=sku.strip())
        if barcode and barcode.strip():
            batch_filter &= Q(product__barcode__iexact=barcode.strip())
        if search and search.strip():
            s = search.strip()
            batch_filter &= (
                Q(product__name__icontains=s)
                | Q(product__sku__icontains=s)
                | Q(product__barcode__icontains=s)
            )

        has_sales_filter = bool(seller_id or customer_id)
        sales_product_ids = {k[1] for k in sales_map.keys()}

        if has_sales_filter:
            batch_filter &= Q(product_id__in=sales_product_ids)
        else:
            batch_filter &= (Q(quantity__gt=0) | Q(product_id__in=sales_product_ids))

        batch_qs = (
            ProductBatch.objects
            .filter(batch_filter)
            .select_related(
                "store", "product", "product__category", "product__brand", "product__unit_measurement"
            )
        )
        batches_map: dict[tuple[int, int], ProductBatch] = {
            (b.store_id, b.product_id): b for b in batch_qs
        }

        if has_sales_filter:
            all_keys = sorted(list(sales_map.keys()), key=lambda k: (k[0], k[1]))
        else:
            all_keys = sorted(
                list(set(batches_map.keys()) | set(sales_map.keys())),
                key=lambda k: (k[0], k[1]),
            )

        period_days = max(1, (end.date() - start.date()).days)
        period_days_dec = Decimal(str(period_days))

        rows = []
        for key in all_keys:
            s = sales_map.get(key)
            b = batches_map.get(key)

            # Agar bu do'konda sotuv bo'lmagan va qoldiq ham <= 0 bo'lsa, o'tkazib yuboramiz
            if s is None and (b is None or (b.quantity or Decimal("0")) <= Decimal("0")):
                continue

            if s is not None:
                store_id_val = s["store_id"]
                store_name = s["store"]
                p_id = s["product_id"]
                p_name = s["name"]
                sku_val = s["sku"]
                barcode_val = s["barcode"]
                cat_name = s["category"]
                b_name = s["brand"]
                unit_val = s["unit"]

                sold_qty = s["sold_qty"]
                returned_qty = s["returned_qty"]
                net_sold_qty = s["net_sold_qty"]
                gross_sales = s["gross_sales"]
                discount = s["discount"]
                net_revenue = s["net_revenue"]
                unit_cost = s["unit_cost"]
                total_cost = s["total_cost"]
                profit = s["profit"]
                margin_pct = s["margin_pct"]
                has_missing_cost = s["has_missing_cost"]
            else:
                # Dead stock — davrda sotuv bo'lmagan, lekin omborda qoldiq bor
                store_id_val = b.store_id
                store_name = b.store.name if b.store else "-"
                p = b.product
                p_id = p.id
                p_name = p.name or "-"
                sku_val = p.sku or "-"
                barcode_val = p.barcode or "-"
                cat_name = p.category.name if p.category else "-"
                b_name = p.brand.name if p.brand else "-"
                unit_val = p.unit_measurement.measurement if p.unit_measurement else "-"

                sold_qty = Decimal("0.00")
                returned_qty = Decimal("0.00")
                net_sold_qty = Decimal("0.00")
                gross_sales = Decimal("0.00")
                discount = Decimal("0.00")
                net_revenue = Decimal("0.00")
                unit_cost = Decimal(str(b.purchase_price or 0)).quantize(Decimal("0.01"))
                total_cost = Decimal("0.00")
                profit = Decimal("0.00")
                margin_pct = Decimal("0.0")
                has_missing_cost = bool(not b.purchase_price or b.purchase_price == 0)

            current_stock = Decimal(str(b.quantity if b and b.quantity is not None else 0))

            # 1. Kunlik sotuv tezligi (Sales Velocity)
            sales_velocity = (net_sold_qty / period_days_dec).quantize(Decimal("0.01"))

            # 2. Qoldiq kunlari (Days of Inventory - DOI)
            if sales_velocity > Decimal("0.00"):
                if current_stock <= Decimal("0.00"):
                    doi = Decimal("0.0")
                else:
                    doi = (current_stock / sales_velocity).quantize(Decimal("0.1"))
            else:
                doi = None

            # 3. Samaradorlik holati (Efficiency Status)
            # Rasmiy qoidalar:
            # 1. net_sold_qty < 0 -> net_return
            # 2. current_stock <= 0 AND net_sold_qty >= 0 -> out_of_stock
            # 3. current_stock > 0 AND net_sold_qty == 0 -> dead_stock
            # 4. current_stock > 0 AND net_sold_qty > 0 -> active
            if net_sold_qty < Decimal("0.00"):
                status_code = "net_return"
            elif current_stock <= Decimal("0.00"):
                status_code = "out_of_stock"
            elif net_sold_qty == Decimal("0.00"):
                status_code = "dead_stock"
            else:
                status_code = "active"

            row = {
                "store_id": store_id_val,
                "store": store_name,
                "product_id": p_id,
                "name": p_name,
                "sku": sku_val,
                "barcode": barcode_val,
                "category": cat_name,
                "brand": b_name,
                "unit": unit_val,
                "current_stock": current_stock,
                "sold_qty": sold_qty,
                "returned_qty": returned_qty,
                "net_sold_qty": net_sold_qty,
                "gross_sales": gross_sales,
                "discount": discount,
                "net_revenue": net_revenue,
                "revenue_share_pct": Decimal("0.0"),  # Quyida filtrlangan scope bo'yicha hisoblanadi
                "unit_cost": unit_cost,
                "total_cost": total_cost,
                "profit": profit,
                "margin_pct": margin_pct,
                "sales_velocity": sales_velocity,
                "doi": doi,
                "efficiency_status": status_code,
                "efficiency_status_display": EFFICIENCY_STATUS_LABELS.get(status_code, status_code),
                "has_missing_cost": has_missing_cost,
            }
            rows.append(row)

        # Holat bo'yicha filtr
        if efficiency_status and efficiency_status in EFFICIENCY_STATUS_LABELS:
            rows = [r for r in rows if r["efficiency_status"] == efficiency_status]

        # 4. Tushum ulushi % (Revenue Share) — filtrlangan hisobot doirasi (scope) bo'yicha
        total_scope_net_revenue = sum((r["net_revenue"] for r in rows), Decimal("0.00"))
        for r in rows:
            if total_scope_net_revenue != Decimal("0.00"):
                r["revenue_share_pct"] = (
                    (r["net_revenue"] / total_scope_net_revenue) * Decimal("100")
                ).quantize(Decimal("0.1"))
            else:
                r["revenue_share_pct"] = Decimal("0.0")

        # Saralash
        if sort_by == "quantity":
            rows.sort(key=lambda x: (-x["net_sold_qty"], -x["net_revenue"]))
        elif sort_by == "profit":
            rows.sort(key=lambda x: (-x["profit"], -x["net_revenue"]))
        elif sort_by == "velocity":
            rows.sort(key=lambda x: (-x["sales_velocity"], -x["net_revenue"]))
        elif sort_by == "stock":
            rows.sort(key=lambda x: (-x["current_stock"], -x["net_revenue"]))
        elif sort_by == "doi":
            rows.sort(key=lambda x: (x["doi"] is None, x["doi"] if x["doi"] is not None else Decimal("999999"), -x["net_revenue"]))
        else:  # revenue default
            rows.sort(key=lambda x: (-x["net_revenue"], -x["sold_qty"]))

        # Yig'ma ko'rsatkichlar
        total_current_stock = sum((r["current_stock"] for r in rows), Decimal("0.00"))
        total_sold_qty = sum((r["sold_qty"] for r in rows), Decimal("0.00"))
        total_ret_qty = sum((r["returned_qty"] for r in rows), Decimal("0.00"))
        total_net_sold_qty = sum((r["net_sold_qty"] for r in rows), Decimal("0.00"))
        total_gross_sales = sum((r["gross_sales"] for r in rows), Decimal("0.00"))
        total_discount = sum((r["discount"] for r in rows), Decimal("0.00"))
        total_net_revenue = sum((r["net_revenue"] for r in rows), Decimal("0.00"))
        total_cost = sum((r["total_cost"] for r in rows), Decimal("0.00"))
        total_profit = sum((r["profit"] for r in rows), Decimal("0.00"))

        overall_margin = (
            ((total_profit / total_net_revenue) * Decimal("100")).quantize(Decimal("0.1"))
            if total_net_revenue > Decimal("0.00")
            else Decimal("0.0")
        )

        active_count = sum(1 for r in rows if r["efficiency_status"] == "active")
        dead_stock_count = sum(1 for r in rows if r["efficiency_status"] == "dead_stock")
        out_of_stock_count = sum(1 for r in rows if r["efficiency_status"] == "out_of_stock")
        net_return_count = sum(1 for r in rows if r["efficiency_status"] == "net_return")
        any_missing_cost = any(r["has_missing_cost"] for r in rows)

        totals = {
            "count": len(rows),
            "total_current_stock": total_current_stock,
            "total_sold_qty": total_sold_qty,
            "total_ret_qty": total_ret_qty,
            "total_net_sold_qty": total_net_sold_qty,
            "total_gross_sales": total_gross_sales,
            "total_discount": total_discount,
            "total_net_revenue": total_net_revenue,
            "total_cost": total_cost,
            "total_profit": total_profit,
            "overall_margin": overall_margin,
            "active_count": active_count,
            "dead_stock_count": dead_stock_count,
            "out_of_stock_count": out_of_stock_count,
            "net_return_count": net_return_count,
            "any_missing_cost": any_missing_cost,
        }

        return rows, totals

    # ─────────────────────────────────────────────────────────────
    # 6. ABC Tahlili (ABC Analysis - Pareto 80/15/5)
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_abc_analysis_metrics(
        start: datetime,
        end: datetime,
        store_id: int | None = None,
        category_id: int | None = None,
        brand_id: int | None = None,
        product_id: int | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        seller_id: int | None = None,
        customer_id: int | None = None,
        search: str | None = None,
        metric: str = "revenue",
        abc_class: str | None = None,
    ) -> tuple[list[dict], dict]:
        """
        ABC tahlili hisob-kitoblarini amalga oshiradi.

        Qoidalar:
          - Metrikalar: 'revenue' (sukut), 'profit', 'quantity'
          - Saralash: ORDER BY metric DESC, name ASC
          - Jami musbat metrika: total_positive_metric = sum(m for m in metrics if m > 0)
          - Mahsulot ulushi: share = (m / total_positive_metric) * 100 agar m > 0, aks holda 0.0
          - Kumulyativ yig'indi: running sum of share (faqat musbat tovarlar)
          - Nol va manfiy tovarlar: Paretto tartibini buzmasligi uchun share = 0.0,
            cumulative = 100.0 (agar musbat bo'lsa) aks holda 0.0, toifasi esa 'C'.
          - Toifalar:
              A: 0% < cumulative <= 80% (va 1-musbat tovar har doim kamida 'A')
              B: 80% < cumulative <= 95%
              C: 95% < cumulative <= 100% (va nol/manfiy metrikali barcha tovarlar)
        """
        base_rows, _ = ReportingFoundationService.get_sales_by_product_metrics(
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
            sort_by="revenue",
        )

        metric_normalized = (metric or "revenue").strip().lower()
        if metric_normalized not in ("revenue", "profit", "quantity"):
            metric_normalized = "revenue"

        # Har bir qatorda tanlangan metrika qiymatini belgilash
        for r in base_rows:
            if metric_normalized == "profit":
                val = r["profit"]
            elif metric_normalized == "quantity":
                val = r["net_sold_qty"]
            else:
                val = r["net_revenue"]
            r["metric_value"] = val

        # Saralash: metric DESC, name ASC (deterministik reyting)
        base_rows.sort(key=lambda r: (-r["metric_value"], (r.get("name") or "").lower()))

        # Faqat musbat ko'rsatkichlar yig'indisi
        total_positive = sum(
            (r["metric_value"] for r in base_rows if r["metric_value"] > Decimal("0")),
            Decimal("0.00"),
        )

        positive_count = sum(1 for r in base_rows if r["metric_value"] > Decimal("0"))
        running_sum = Decimal("0.00")
        processed_positive = 0

        for r in base_rows:
            val = r["metric_value"]
            if val > Decimal("0") and total_positive > Decimal("0"):
                share = ((val / total_positive) * Decimal("100")).quantize(Decimal("0.01"))
                running_sum += share
                processed_positive += 1
                if processed_positive == positive_count or running_sum > Decimal("100.00"):
                    cumulative = Decimal("100.00")
                else:
                    cumulative = running_sum.quantize(Decimal("0.01"))

                # Pareto chegaralari:
                # 1-tovar har doim kamida 'A' (agar yakka o'zi > 80% bo'lsa ham)
                if processed_positive == 1:
                    assigned_class = "A"
                elif cumulative <= Decimal("80.00"):
                    assigned_class = "A"
                elif cumulative <= Decimal("95.00"):
                    assigned_class = "B"
                else:
                    assigned_class = "C"

                r["share_pct"] = float(share)
                r["cumulative_pct"] = float(cumulative)
                r["abc_class"] = assigned_class
            else:
                # Nol yoki manfiy metrikali tovarlar (Paretto tartibini buzmaydi)
                r["share_pct"] = 0.0
                r["cumulative_pct"] = 100.0 if total_positive > Decimal("0") else 0.0
                r["abc_class"] = "C"

        # Umumiy portfel yig'indilari
        total_count = len(base_rows)
        count_a = sum(1 for r in base_rows if r["abc_class"] == "A")
        count_b = sum(1 for r in base_rows if r["abc_class"] == "B")
        count_c = sum(1 for r in base_rows if r["abc_class"] == "C")

        sum_metric_a = sum((r["metric_value"] for r in base_rows if r["abc_class"] == "A"), Decimal("0.00"))
        sum_metric_b = sum((r["metric_value"] for r in base_rows if r["abc_class"] == "B"), Decimal("0.00"))
        sum_metric_c = sum((r["metric_value"] for r in base_rows if r["abc_class"] == "C"), Decimal("0.00"))

        share_a_metric = (
            ((sum_metric_a / total_positive) * Decimal("100")).quantize(Decimal("0.1"))
            if total_positive > Decimal("0") and sum_metric_a > Decimal("0")
            else Decimal("0.0")
        )
        share_b_metric = (
            ((sum_metric_b / total_positive) * Decimal("100")).quantize(Decimal("0.1"))
            if total_positive > Decimal("0") and sum_metric_b > Decimal("0")
            else Decimal("0.0")
        )
        share_c_metric = (
            ((sum_metric_c / total_positive) * Decimal("100")).quantize(Decimal("0.1"))
            if total_positive > Decimal("0") and sum_metric_c > Decimal("0")
            else Decimal("0.0")
        )

        pct_count_a = round((count_a / total_count) * 100, 1) if total_count > 0 else 0.0
        pct_count_b = round((count_b / total_count) * 100, 1) if total_count > 0 else 0.0
        pct_count_c = round((count_c / total_count) * 100, 1) if total_count > 0 else 0.0

        totals = {
            "total_count": total_count,
            "metric": metric_normalized,
            "total_positive_metric": total_positive,
            "count_a": count_a,
            "count_b": count_b,
            "count_c": count_c,
            "pct_count_a": pct_count_a,
            "pct_count_b": pct_count_b,
            "pct_count_c": pct_count_c,
            "sum_metric_a": sum_metric_a,
            "sum_metric_b": sum_metric_b,
            "sum_metric_c": sum_metric_c,
            "share_a_metric": share_a_metric,
            "share_b_metric": share_b_metric,
            "share_c_metric": share_c_metric,
        }

        # Agar abc_class bo'yicha filtr berilgan bo'lsa
        if abc_class and abc_class.strip():
            target_class = abc_class.strip().upper()
            rows = [r for r in base_rows if r["abc_class"] == target_class]
        else:
            rows = base_rows

        return rows, totals

    @staticmethod
    def get_inventory_results_metrics(
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        store_id: int | None = None,
        allowed_store_ids: list[int] | None = None,
        session_id: int | None = None,
        product_id: int | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        category_id: int | None = None,
        brand_id: int | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> tuple[list[dict], dict]:
        """
        Inventarizatsiya natijalari va kamomad/ortiqcha tahlili (Phase 1.4).

        Source-of-truth tamoyillari:
          - Expected Qty FAQAT `InventorySnapshot.expected_quantity` dan olinadi (ProductBatch dan emas).
          - Counted Qty FAQAT `InventoryCount.counted_quantity` dan olinadi.
          - `is_check=False` tovarlar kamomad/nol deb hisoblanmaydi; ularning statusi 'unchecked',
            difference_qty=None, shortage_qty=0, excess_qty=0 bo'ladi.
          - Faqat 'completed' sessiyalar hisobotga kiritiladi.
          - Final balance: `finalize()` biznes logikasi bilan 100% bir xil:
            counted - sold_out - transfer_out + transfer_in + entry + returned (faqat created_at > counted_at).
          - Unit cost:
            Kamomad uchun: `WriteOffItem.purchase_price` snapshot (mavjud bo'lsa).
            Ortiqcha / fallback uchun: `ProductBatch.purchase_price` (tarixiy excess narx modeli yo'q).
        """
        empty_totals = {
            "total_expected_qty": 0.0,
            "total_counted_qty": 0.0,
            "total_shortage_qty": 0.0,
            "total_excess_qty": 0.0,
            "total_shortage_value": 0.0,
            "total_excess_value": 0.0,
            "net_difference_value": 0.0,
            "matched_count": 0,
            "shortage_count": 0,
            "excess_count": 0,
            "unchecked_count": 0,
            "total_rows": 0,
        }

        session_filter = Q(status=InventorySession.Status.COMPLETED)
        if start:
            session_filter &= Q(started_at__gte=start)
        if end:
            session_filter &= Q(started_at__lte=end)
        if store_id:
            session_filter &= Q(store_id=store_id)
        if allowed_store_ids is not None:
            session_filter &= Q(store_id__in=allowed_store_ids)
        if session_id:
            session_filter &= Q(id=session_id)

        session_ids = list(
            InventorySession.objects.filter(session_filter).values_list("id", flat=True)
        )
        if not session_ids:
            return [], empty_totals

        snapshot_filter = Q(session_id__in=session_ids)
        if product_id:
            snapshot_filter &= Q(product_id=product_id)
        if sku and sku.strip():
            snapshot_filter &= Q(product__sku__iexact=sku.strip())
        if barcode and barcode.strip():
            snapshot_filter &= Q(product__barcode__iexact=barcode.strip())
        if category_id:
            snapshot_filter &= Q(product__category_id=category_id)
        if brand_id:
            snapshot_filter &= Q(product__brand_id=brand_id)
        if search and search.strip():
            s = search.strip()
            snapshot_filter &= (
                Q(product__name__icontains=s)
                | Q(product__sku__icontains=s)
                | Q(product__barcode__icontains=s)
            )

        snapshots = list(
            InventorySnapshot.objects
            .filter(snapshot_filter)
            .select_related(
                "session",
                "session__store",
                "product",
                "product__category",
                "product__brand",
                "product__unit_measurement",
            )
            .order_by("-session__id", "product__name")
        )
        if not snapshots:
            return [], empty_totals

        target_session_ids = {s.session_id for s in snapshots}
        target_product_ids = {s.product_id for s in snapshots}
        target_store_ids = {s.store_id for s in snapshots}

        # 1. InventoryCount larni o'qish (session_id, product_id bo'yicha)
        counts = InventoryCount.objects.filter(
            session_id__in=target_session_ids,
            product_id__in=target_product_ids,
        ).values("session_id", "product_id", "counted_quantity", "is_check", "counted_at")

        count_map = {
            (c["session_id"], c["product_id"]): c
            for c in counts
        }

        # 2. InventoryMovement (faqat is_check=True va created_at > counted_at)
        movements = InventoryMovement.objects.filter(
            session_id__in=target_session_ids,
            product_id__in=target_product_ids,
        ).values("session_id", "product_id", "type", "quantity", "created_at")

        movement_buckets: dict[tuple[int, int], dict[str, Decimal]] = {}
        for mv in movements:
            key = (mv["session_id"], mv["product_id"])
            cnt = count_map.get(key)
            if not cnt or not cnt.get("is_check"):
                continue
            counted_at = cnt.get("counted_at")
            if counted_at is not None and mv["created_at"] <= counted_at:
                continue
            bucket = movement_buckets.setdefault(key, {
                "sold_out": Decimal("0"),
                "returned": Decimal("0"),
                "transfer_out": Decimal("0"),
                "transfer_in": Decimal("0"),
                "entry": Decimal("0"),
            })
            mtype = mv["type"]
            if mtype == "s":
                bucket["sold_out"] += mv["quantity"]
            elif mtype == "r":
                bucket["returned"] += mv["quantity"]
            elif mtype == "to":
                bucket["transfer_out"] += mv["quantity"]
            elif mtype == "ti":
                bucket["transfer_in"] += mv["quantity"]
            elif mtype == "e":
                bucket["entry"] += mv["quantity"]

        # 3. WriteOffItem dan kamomad tannarxini olish (linked to session)
        writeoff_items = WriteOffItem.objects.filter(
            write_off__inventory_session_id__in=target_session_ids,
            write_off__reason=WriteOff.Reason.INVENTORY,
            product_id__in=target_product_ids,
        ).values("write_off__inventory_session_id", "product_id", "purchase_price")

        writeoff_cost_map = {
            (w["write_off__inventory_session_id"], w["product_id"]): w["purchase_price"]
            for w in writeoff_items
        }

        # 4. ProductBatch dan tannarx (excess yoki fallback)
        batch_items = ProductBatch.objects.filter(
            store_id__in=target_store_ids,
            product_id__in=target_product_ids,
        ).values("store_id", "product_id", "purchase_price")

        batch_cost_map = {
            (b["store_id"], b["product_id"]): (b["purchase_price"] or Decimal("0.00"))
            for b in batch_items
        }

        rows = []
        for s in snapshots:
            sess = s.session
            prod = s.product
            key = (s.session_id, s.product_id)
            cnt = count_map.get(key)
            is_check = cnt.get("is_check", False) if cnt else False
            expected_qty = s.expected_quantity or Decimal("0")

            if not is_check:
                status_code = "unchecked"
                counted_qty = None
                difference_qty = None
                shortage_qty = Decimal("0")
                excess_qty = Decimal("0")
                unit_cost = batch_cost_map.get((s.store_id, s.product_id), Decimal("0.00"))
                shortage_value = Decimal("0.00")
                excess_value = Decimal("0.00")
                final_balance = expected_qty
            else:
                counted_qty = cnt.get("counted_quantity", Decimal("0")) if cnt else Decimal("0")
                difference_qty = counted_qty - expected_qty

                # Sanoqdan keyingi harakatlar bo'yicha yakuniy balans
                mv_data = movement_buckets.get(key, {})
                sold_out = mv_data.get("sold_out", Decimal("0"))
                returned = mv_data.get("returned", Decimal("0"))
                transfer_out = mv_data.get("transfer_out", Decimal("0"))
                transfer_in = mv_data.get("transfer_in", Decimal("0"))
                entry = mv_data.get("entry", Decimal("0"))

                final_balance = (
                    counted_qty
                    - sold_out
                    - transfer_out
                    + transfer_in
                    + entry
                    + returned
                )

                if counted_qty == expected_qty:
                    status_code = "matched"
                    shortage_qty = Decimal("0")
                    excess_qty = Decimal("0")
                    unit_cost = batch_cost_map.get((s.store_id, s.product_id), Decimal("0.00"))
                    shortage_value = Decimal("0.00")
                    excess_value = Decimal("0.00")
                elif counted_qty < expected_qty:
                    status_code = "shortage"
                    shortage_qty = expected_qty - counted_qty
                    excess_qty = Decimal("0")
                    unit_cost = writeoff_cost_map.get(key) or batch_cost_map.get((s.store_id, s.product_id), Decimal("0.00"))
                    shortage_value = (shortage_qty * unit_cost).quantize(Decimal("0.01"))
                    excess_value = Decimal("0.00")
                else:
                    status_code = "excess"
                    shortage_qty = Decimal("0")
                    excess_qty = counted_qty - expected_qty
                    unit_cost = batch_cost_map.get((s.store_id, s.product_id), Decimal("0.00"))
                    shortage_value = Decimal("0.00")
                    excess_value = (excess_qty * unit_cost).quantize(Decimal("0.01"))

            # Status bo'yicha filtr
            if status and status.strip() and status.strip().lower() != "all":
                if status_code != status.strip().lower():
                    continue

            rows.append({
                "session_id": sess.id,
                "store_id": sess.store_id,
                "store_name": sess.store.name if sess.store else "",
                "session_date": sess.started_at.strftime("%d.%m.%Y %H:%M") if sess.started_at else "",
                "product_id": prod.id,
                "product_name": prod.name,
                "sku": prod.sku or "",
                "barcode": prod.barcode or "",
                "unit": prod.unit_measurement.measurement if prod.unit_measurement else "dona",
                "category_id": prod.category_id,
                "category_name": prod.category.name if prod.category else "-",
                "brand_id": prod.brand_id,
                "brand_name": prod.brand.name if prod.brand else "-",
                "expected_qty": float(expected_qty),
                "counted_qty": float(counted_qty) if counted_qty is not None else None,
                "difference_qty": float(difference_qty) if difference_qty is not None else None,
                "shortage_qty": float(shortage_qty),
                "excess_qty": float(excess_qty),
                "unit_cost": float(unit_cost),
                "shortage_value": float(shortage_value),
                "excess_value": float(excess_value),
                "final_balance": float(final_balance),
                "status": status_code,
            })

        total_expected = sum(r["expected_qty"] for r in rows)
        total_counted = sum(r["counted_qty"] for r in rows if r["counted_qty"] is not None)
        total_shortage_qty = sum(r["shortage_qty"] for r in rows)
        total_excess_qty = sum(r["excess_qty"] for r in rows)
        total_shortage_val = sum(Decimal(str(r["shortage_value"])) for r in rows)
        total_excess_val = sum(Decimal(str(r["excess_value"])) for r in rows)
        net_difference_val = total_excess_val - total_shortage_val

        matched_count = sum(1 for r in rows if r["status"] == "matched")
        shortage_count = sum(1 for r in rows if r["status"] == "shortage")
        excess_count = sum(1 for r in rows if r["status"] == "excess")
        unchecked_count = sum(1 for r in rows if r["status"] == "unchecked")

        totals = {
            "total_expected_qty": total_expected,
            "total_counted_qty": total_counted,
            "total_shortage_qty": total_shortage_qty,
            "total_excess_qty": total_excess_qty,
            "total_shortage_value": float(total_shortage_val),
            "total_excess_value": float(total_excess_val),
            "net_difference_value": float(net_difference_val),
            "matched_count": matched_count,
            "shortage_count": shortage_count,
            "excess_count": excess_count,
            "unchecked_count": unchecked_count,
            "total_rows": len(rows),
        }

        return rows, totals

    @staticmethod
    def get_order_returns_metrics(
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        store_id: int | None = None,
        allowed_store_ids: list[int] | None = None,
        return_id: int | None = None,
        order_id: int | None = None,
        product_id: int | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        category_id: int | None = None,
        brand_id: int | None = None,
        supplier_id: int | None = None,
        seller_id: int | None = None,
        search: str | None = None,
    ) -> tuple[list[dict], dict]:
        """
        Buyurtma qaytarishlari hisoboti (Phase 1.5).

        Source-of-truth tamoyillari:
          - Dataset root: `SaleReturnItem` (granular item-level).
          - Asosiy sana: `SaleReturn.created_at` in [start, end) sargable oraliq.
            Asl `Sale.created_at` qaytarim davrini belgilamaydi!
          - returned_qty -> `SaleReturnItem.quantity`
          - refund_amount -> `SaleReturnItem.total_price` (proportsional chegirma inobatga olingan;
            chegirmani ikkinchi marta hisoblab/ko'paytirib yubormaslik shart!)
          - sale_price -> `SaleReturnItem.unit_price`
          - purchase_price -> `SaleReturnItem.sale_item.purchase_price` (fallback: ProductBatch.purchase_price)
          - sale_value -> returned_qty * sale_price
          - purchase_value -> returned_qty * purchase_price
          - discount_refunded -> sale_value - refund_amount
          - profit_impact -> refund_amount - purchase_value (yo'qotilgan yalpi foyda)
          - supplier -> latest StockEntryItem.entry.supplier (arxitektura cheklovi)
        """
        empty_totals = {
            "total_returns_count": 0,
            "total_rows": 0,
            "total_returned_qty": 0.0,
            "total_sale_value": 0.0,
            "total_refund_amount": 0.0,
            "total_discount_refunded": 0.0,
            "total_purchase_value": 0.0,
            "total_profit_impact": 0.0,
            "has_zero_cost_items": False,
        }

        item_filter = Q(sale_return__sale__deleted_at__isnull=True)
        if start:
            item_filter &= Q(sale_return__created_at__gte=start)
        if end:
            item_filter &= Q(sale_return__created_at__lt=end)
        if store_id:
            item_filter &= Q(sale_return__store_id=store_id)
        if allowed_store_ids is not None:
            item_filter &= Q(sale_return__store_id__in=allowed_store_ids)
        if return_id:
            item_filter &= Q(sale_return__id=return_id)
        if order_id:
            item_filter &= Q(sale_return__sale_id=order_id)
        if product_id:
            item_filter &= Q(product_id=product_id)
        if sku and sku.strip():
            item_filter &= Q(product__sku__iexact=sku.strip())
        if barcode and barcode.strip():
            item_filter &= Q(product__barcode__iexact=barcode.strip())
        if category_id:
            item_filter &= Q(product__category_id=category_id)
        if brand_id:
            item_filter &= Q(product__brand_id=brand_id)
        if seller_id:
            item_filter &= Q(sale_return__seller_id=seller_id)
        if supplier_id:
            item_filter &= Q(
                Exists(
                    StockEntryItem.objects.filter(
                        product_id=OuterRef("product_id"),
                        entry__supplier_id=supplier_id,
                    )
                )
            )
        if search and search.strip():
            s = search.strip()
            search_q = (
                Q(product__name__icontains=s)
                | Q(product__sku__icontains=s)
                | Q(product__barcode__icontains=s)
                | Q(sale_return__customer__full_name__icontains=s)
                | Q(sale_return__customer__phone_number__icontains=s)
            )
            if s.isdigit():
                search_q |= Q(sale_return__sale_id=int(s)) | Q(sale_return__id=int(s))
            item_filter &= search_q

        # Query 1: Asosiy SaleReturnItem so'rovi (barcha 9 ta FK lar bilan bitta JOIN)
        items = list(
            SaleReturnItem.objects
            .filter(item_filter)
            .select_related(
                "sale_return",
                "sale_return__store",
                "sale_return__seller",
                "sale_return__customer",
                "sale_return__sale",
                "sale_item",
                "product",
                "product__category",
                "product__brand",
                "product__unit_measurement",
            )
            .order_by("-sale_return__created_at", "-sale_return__id", "id")
        )
        if not items:
            return [], empty_totals

        target_product_ids = {it.product_id for it in items}
        target_store_ids = {it.sale_return.store_id for it in items}
        target_payment_groups = {it.sale_return.payment_group for it in items if it.sale_return.payment_group}

        # Query 2: Eng oxirgi ta'minotchi xaritasi
        supplier_map = ReportingFoundationService.get_latest_supplier_and_import_map(
            product_ids=target_product_ids
        )

        # Query 3: Partiya tannarxi (faqat purchase_price bo'lmagan qatorlar uchun fallback)
        items_needing_cost = [it for it in items if it.sale_item.purchase_price is None]
        if items_needing_cost:
            batch_qs = ProductBatch.objects.filter(
                store_id__in=target_store_ids,
                product_id__in={it.product_id for it in items_needing_cost},
            ).values("store_id", "product_id", "purchase_price")
            batch_cost_map = {
                (b["store_id"], b["product_id"]): (b["purchase_price"] or Decimal("0.00"))
                for b in batch_qs
            }
        else:
            batch_cost_map = {}

        # Query 4: Qaytarim to'lov usullari (payment_group bo'yicha)
        payment_group_map = {}
        if target_payment_groups:
            refund_payments = list(
                Payment.objects
                .filter(payment_group__in=target_payment_groups, is_refund=True)
                .values("payment_group", "type", "bank_card__name", "amount")
            )
            for p in refund_payments:
                payment_group_map.setdefault(p["payment_group"], []).append(p)

        rows = []
        for it in items:
            ret = it.sale_return
            prod = it.product
            sale_item = it.sale_item
            returned_qty = it.quantity or Decimal("0")
            unit_sale_price = it.unit_price or Decimal("0.00")
            refund_amount = it.total_price or Decimal("0.00")

            # Tannarx aniqlash
            if sale_item.purchase_price is not None:
                unit_purchase_price = sale_item.purchase_price
            else:
                unit_purchase_price = batch_cost_map.get((ret.store_id, it.product_id), Decimal("0.00"))

            sale_value = (returned_qty * unit_sale_price).quantize(Decimal("0.01"))
            purchase_value = (returned_qty * unit_purchase_price).quantize(Decimal("0.01"))
            raw_discount = sale_value - refund_amount
            discount_refunded = max(Decimal("0.00"), raw_discount).quantize(Decimal("0.01"))
            profit_impact = (refund_amount - purchase_value).quantize(Decimal("0.01"))

            # To'lov usulini aniqlash
            group_payments = payment_group_map.get(ret.payment_group, [])
            if group_payments:
                distinct_types = {p["type"] for p in group_payments}
                money_paid = sum((p["amount"] for p in group_payments), Decimal("0"))
                cards = [p["bank_card__name"] for p in group_payments if p.get("bank_card__name")]
                if money_paid < ret.total_refund:
                    payment_method = "Aralash"
                elif len(distinct_types) > 1:
                    payment_method = "Aralash"
                elif "card" in distinct_types:
                    payment_method = f"Karta ({', '.join(set(cards))})" if cards else "Karta"
                else:
                    payment_method = "Naqd"
            elif ret.payment_group is None and ret.total_refund > 0:
                if ret.customer_id:
                    payment_method = "Qarz"
                else:
                    payment_method = "Naqd"
            elif ret.total_refund == 0:
                payment_method = "—"
            else:
                payment_method = "Naqd"

            supplier_info = supplier_map.get(prod.id, {})
            seller = ret.seller
            seller_name = (seller.full_name or seller.phone_number) if seller else "-"
            customer = ret.customer
            customer_name = customer.full_name if customer else "—"

            rows.append({
                "return_id": ret.id,
                "order_id": ret.sale_id,
                "store_id": ret.store_id,
                "store_name": ret.store.name if ret.store else "-",
                "return_datetime": ret.created_at.strftime("%d.%m.%Y %H:%M") if ret.created_at else "-",
                "return_timestamp": ret.created_at.isoformat() if ret.created_at else "",
                "seller_id": ret.seller_id,
                "seller_name": seller_name,
                "customer_id": ret.customer_id,
                "customer_name": customer_name,
                "product_id": prod.id,
                "product_name": prod.name,
                "sku": prod.sku or "-",
                "barcode": prod.barcode or "-",
                "brand_id": prod.brand_id,
                "brand_name": prod.brand.name if prod.brand else "-",
                "category_id": prod.category_id,
                "category_name": prod.category.name if prod.category else "-",
                "unit": prod.unit_measurement.measurement if prod.unit_measurement else "dona",
                "supplier_name": supplier_info.get("supplier") or "-",
                "returned_qty": float(returned_qty),
                "unit_sale_price": float(unit_sale_price),
                "unit_purchase_price": float(unit_purchase_price),
                "sale_value": float(sale_value),
                "discount_refunded": float(discount_refunded),
                "refund_amount": float(refund_amount),
                "purchase_value": float(purchase_value),
                "profit_impact": float(profit_impact),
                "payment_method": payment_method,
                "comment": ret.comment or "",
            })

        unique_return_ids = {r["return_id"] for r in rows}
        total_returns_count = len(unique_return_ids)
        total_rows = len(rows)
        total_returned_qty = sum(r["returned_qty"] for r in rows)
        total_sale_value = sum(Decimal(str(r["sale_value"])) for r in rows)
        total_refund_amount = sum(Decimal(str(r["refund_amount"])) for r in rows)
        total_discount_refunded = sum(Decimal(str(r["discount_refunded"])) for r in rows)
        total_purchase_value = sum(Decimal(str(r["purchase_value"])) for r in rows)
        total_profit_impact = sum(Decimal(str(r["profit_impact"])) for r in rows)
        has_zero_cost = any(r["unit_purchase_price"] == 0 for r in rows)

        totals = {
            "total_returns_count": total_returns_count,
            "total_rows": total_rows,
            "total_returned_qty": float(total_returned_qty),
            "total_sale_value": float(total_sale_value),
            "total_refund_amount": float(total_refund_amount),
            "total_discount_refunded": float(total_discount_refunded),
            "total_purchase_value": float(total_purchase_value),
            "total_profit_impact": float(total_profit_impact),
            "has_zero_cost_items": has_zero_cost,
        }

        return rows, totals

    @classmethod
    def get_write_offs_metrics(
        cls,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        store_id: int | None = None,
        allowed_store_ids: list[int] | None = None,
        write_off_id: int | None = None,
        reason: str | None = None,
        product_id: int | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        category_id: int | None = None,
        brand_id: int | None = None,
        supplier_id: int | None = None,
        user_id: int | None = None,
        inventory_session_id: int | None = None,
        search: str | None = None,
    ) -> tuple[list[dict], dict]:
        """
        Hisobdan chiqarishlar (Write-offs) hisoboti (Phase 1.6).

        Source-of-truth tamoyillari:
          - Dataset root: `WriteOffItem` (granular item-level).
          - Pair: WriteOff + WriteOffItem (StockAdjustment aralashtirilmaydi).
          - Asosiy sana: `WriteOff.created_at` in [start, end) sargable oraliq.
          - quantity -> `WriteOffItem.quantity`
          - unit_purchase_price -> `WriteOffItem.purchase_price` (hujjat yaratilgan paytdagi snapshot)
          - unit_sale_price -> `WriteOffItem.selling_price` (hujjat yaratilgan paytdagi snapshot)
          - purchase_value -> quantity * unit_purchase_price
          - sale_value -> quantity * unit_sale_price
          - profit_impact -> sale_value - purchase_value (yo'qotilgan marja / foyda)
          - supplier -> latest StockEntryItem.entry.supplier (arxitektura cheklovi bo'yicha)
        """
        empty_totals = {
            "total_write_offs_count": 0,
            "total_rows": 0,
            "total_written_off_qty": 0.0,
            "total_purchase_value": 0.0,
            "total_sale_value": 0.0,
            "total_profit_impact": 0.0,
            "has_zero_cost_items": False,
        }

        item_filter = Q()
        if start:
            item_filter &= Q(write_off__created_at__gte=start)
        if end:
            item_filter &= Q(write_off__created_at__lt=end)
        if store_id:
            item_filter &= Q(write_off__store_id=store_id)
        if allowed_store_ids is not None:
            item_filter &= Q(write_off__store_id__in=allowed_store_ids)
        if write_off_id:
            item_filter &= Q(write_off__id=write_off_id)
        if reason and reason.strip():
            item_filter &= Q(write_off__reason=reason.strip())
        if product_id:
            item_filter &= Q(product_id=product_id)
        if sku and sku.strip():
            item_filter &= Q(product__sku__iexact=sku.strip())
        if barcode and barcode.strip():
            item_filter &= Q(product__barcode__iexact=barcode.strip())
        if category_id:
            item_filter &= Q(product__category_id=category_id)
        if brand_id:
            item_filter &= Q(product__brand_id=brand_id)
        if user_id:
            item_filter &= Q(write_off__created_by_id=user_id)
        if inventory_session_id:
            item_filter &= Q(write_off__inventory_session_id=inventory_session_id)
        if supplier_id:
            item_filter &= Q(
                Exists(
                    StockEntryItem.objects.filter(
                        product_id=OuterRef("product_id"),
                        entry__supplier_id=supplier_id,
                    )
                )
            )
        if search and search.strip():
            s = search.strip()
            search_q = (
                Q(product__name__icontains=s)
                | Q(product__sku__icontains=s)
                | Q(product__barcode__icontains=s)
                | Q(write_off__comment__icontains=s)
            )
            if s.isdigit():
                search_q |= Q(write_off__id=int(s))
            item_filter &= search_q

        # Query 1: Asosiy WriteOffItem so'rovi (barcha bog'liq modellar bilan bitta JOIN)
        items = list(
            WriteOffItem.objects
            .filter(item_filter)
            .select_related(
                "write_off",
                "write_off__store",
                "write_off__created_by",
                "product",
                "product__category",
                "product__brand",
                "product__unit_measurement",
            )
            .order_by("-write_off__created_at", "-write_off__id", "id")
        )
        if not items:
            return [], empty_totals

        target_product_ids = {it.product_id for it in items}

        # Query 2: Eng oxirgi ta'minotchi xaritasi (1 ta so'rov)
        supplier_map = ReportingFoundationService.get_latest_supplier_and_import_map(
            product_ids=target_product_ids
        )

        product_status_map = {
            Product.ProductStatus.ACTIVE: "Faol",
            Product.ProductStatus.INACTIVE: "Nofaol (Arxiv)",
            Product.ProductStatus.DRAFT: "Qoralama",
        }
        reason_map = dict(WriteOff.Reason.choices)

        rows = []
        for it in items:
            wo = it.write_off
            prod = it.product
            qty = it.quantity or Decimal("0")
            unit_purchase_price = it.purchase_price or Decimal("0.00")
            unit_sale_price = it.selling_price or Decimal("0.00")

            purchase_value = (qty * unit_purchase_price).quantize(Decimal("0.01"))
            sale_value = (qty * unit_sale_price).quantize(Decimal("0.01"))
            profit_impact = (sale_value - purchase_value).quantize(Decimal("0.01"))

            created_by = wo.created_by
            created_by_name = (created_by.full_name or created_by.phone_number) if created_by else "-"
            reason_display = reason_map.get(wo.reason, wo.reason or "-")
            status_display = product_status_map.get(prod.status, prod.get_status_display() or prod.status)

            supplier_info = supplier_map.get(prod.id, {})
            supplier_name = supplier_info.get("supplier") or "-"
            unit_name = prod.unit_measurement.measurement if prod.unit_measurement else "dona"

            rows.append({
                "write_off_id": wo.id,
                "store_id": wo.store_id,
                "store_name": wo.store.name if wo.store else "-",
                "write_off_datetime": wo.created_at.strftime("%d.%m.%Y %H:%M") if wo.created_at else "-",
                "write_off_timestamp": wo.created_at.isoformat() if wo.created_at else "",
                "reason": wo.reason,
                "reason_display": reason_display,
                "created_by_id": wo.created_by_id,
                "created_by_name": created_by_name,
                "product_id": prod.id,
                "product_name": prod.name,
                "sku": prod.sku or "-",
                "barcode": prod.barcode or "-",
                "brand_id": prod.brand_id,
                "brand_name": prod.brand.name if prod.brand else "-",
                "category_id": prod.category_id,
                "category_name": prod.category.name if prod.category else "-",
                "unit": unit_name,
                "supplier_name": supplier_name,
                "product_status": status_display,
                "quantity": float(qty),
                "unit_purchase_price": float(unit_purchase_price),
                "unit_sale_price": float(unit_sale_price),
                "purchase_value": float(purchase_value),
                "sale_value": float(sale_value),
                "profit_impact": float(profit_impact),
                "inventory_session_id": wo.inventory_session_id,
                "comment": wo.comment or "",
            })

        unique_write_off_ids = {r["write_off_id"] for r in rows}
        total_write_offs_count = len(unique_write_off_ids)
        total_rows = len(rows)
        total_written_off_qty = sum(r["quantity"] for r in rows)
        total_purchase_value = sum(Decimal(str(r["purchase_value"])) for r in rows)
        total_sale_value = sum(Decimal(str(r["sale_value"])) for r in rows)
        total_profit_impact = sum(Decimal(str(r["profit_impact"])) for r in rows)
        has_zero_cost = any(r["unit_purchase_price"] == 0 for r in rows)

        totals = {
            "total_write_offs_count": total_write_offs_count,
            "total_rows": total_rows,
            "total_written_off_qty": float(total_written_off_qty),
            "total_purchase_value": float(total_purchase_value),
            "total_sale_value": float(total_sale_value),
            "total_profit_impact": float(total_profit_impact),
            "has_zero_cost_items": has_zero_cost,
        }

        return rows, totals

    @staticmethod
    def get_stock_entry_returns_map(item_ids: list[int]) -> dict[int, dict]:
        """
        StockEntryItem IDlari bo'yicha qaytarilgan tovarlar agregatsiyasi (1 ta guruhlangan so'rov).
        Returns: {entry_item_id: {"returned_qty": Decimal, "returned_value": Decimal}}
        """
        if not item_ids:
            return {}
        returns_qs = (
            StockEntryReturnItem.objects
            .filter(entry_item_id__in=item_ids)
            .values("entry_item_id")
            .annotate(
                returned_qty=Sum("quantity"),
                returned_value=Sum("amount"),
            )
        )
        return {r["entry_item_id"]: r for r in returns_qs}

    @staticmethod
    def get_stock_entry_transactions_map(entry_ids: list[int]) -> dict[int, dict]:
        """
        StockEntry IDlari bo'yicha yetkazib beruvchi tranzaksiyalari (in, pay, ret) agregatsiyasi.
        Returns: {entry_id: {"total_in": Decimal, "total_paid": Decimal, "total_ret": Decimal}}
        """
        if not entry_ids:
            return {}
        tx_qs = (
            SupplierTransaction.objects
            .filter(entry_id__in=entry_ids)
            .values("entry_id")
            .annotate(
                total_in=Sum("amount", filter=Q(type=SupplierTransaction.TransactionType.INVENTORY_IN)),
                total_paid=Sum("amount", filter=Q(type=SupplierTransaction.TransactionType.PAYMENT)),
                total_ret=Sum("amount", filter=Q(type=SupplierTransaction.TransactionType.RETURN)),
            )
        )
        return {t["entry_id"]: t for t in tx_qs}

    @staticmethod
    def calculate_stock_entry_payment_status(entry: StockEntry, tx_info: dict | None = None) -> str:
        """
        StockEntry va SupplierTransaction ma'lumotlari asosida to'lov holatini aniqlash:
        paid / partial / unpaid.

        Formula SupplierPaymentService.get_remaining_debt biznes mantig'i bilan 100% uyg'un:
        remaining_debt = effective_in - total_paid - total_ret
        """
        tx = tx_info or {}
        total_in = tx.get("total_in") or Decimal("0.00")
        total_paid = tx.get("total_paid") or Decimal("0.00")
        total_ret = tx.get("total_ret") or Decimal("0.00")
        initial_paid = entry.paid_amount or Decimal("0.00")
        initial_debt = entry.debt_amount or Decimal("0.00")

        # Agar tranzaksiyalarda 'in' bo'lmasa, entry.debt_amount dan olinadi
        effective_in = total_in if total_in > 0 else initial_debt
        remaining_debt = effective_in - total_paid - total_ret

        if remaining_debt <= 0:
            return "paid"
        if total_paid > 0 or total_ret > 0 or initial_paid > 0:
            return "partial"
        return "unpaid"

    @classmethod
    def get_imports_metrics(
        cls,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        store_id: int | None = None,
        allowed_store_ids: list[int] | None = None,
        entry_id: int | None = None,
        supplier_id: int | None = None,
        payment_status: str | None = None,
        user_id: int | None = None,
        product_id: int | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        category_id: int | None = None,
        brand_id: int | None = None,
        has_returns: bool | str | None = None,
        search: str | None = None,
    ) -> tuple[list[dict], dict]:
        """
        Kirimlar (Importlar) hisoboti (Phase 1.7).

        Source-of-truth tamoyillari:
          - Dataset root: `StockEntryItem` (granular item-level).
          - Grain: 1 qator = 1 StockEntryItem.
          - Asosiy sana: `StockEntry.created_at` in [start, end) sargable oraliq.
          - quantity -> `StockEntryItem.quantity`
          - unit_purchase_price -> `StockEntryItem.purchase_price` (hujjat yaratilgan paytdagi snapshot)
          - unit_sale_price -> `StockEntryItem.selling_price` (hujjat yaratilgan paytdagi snapshot)
          - unit_wholesale_price -> `StockEntryItem.wholesale_price` (hujjat yaratilgan paytdagi snapshot)
          - purchase_value -> quantity * unit_purchase_price
          - sale_value -> quantity * unit_sale_price
          - potential_margin -> sale_value - purchase_value
          - returned_qty -> SUM(StockEntryReturnItem.quantity)
          - returned_value -> SUM(StockEntryReturnItem.amount)
          - net_quantity -> quantity - returned_qty
          - net_purchase_value -> purchase_value - returned_value
          - payment_status -> entry va SupplierTransaction holatiga ko'ra: paid / partial / unpaid
          - supplier -> StockEntry.supplier (100% to'g'ridan-to'g'ri FK)
        """
        empty_totals = {
            "total_entries_count": 0,
            "total_rows": 0,
            "total_received_qty": 0.0,
            "total_purchase_value": 0.0,
            "total_sale_value": 0.0,
            "total_potential_margin": 0.0,
            "total_returned_qty": 0.0,
            "total_returned_value": 0.0,
            "total_net_purchase_value": 0.0,
            "has_zero_cost_items": False,
        }

        item_filter = Q()
        if start:
            item_filter &= Q(entry__created_at__gte=start)
        if end:
            item_filter &= Q(entry__created_at__lt=end)
        if store_id:
            item_filter &= Q(entry__store_id=store_id)
        if allowed_store_ids is not None:
            item_filter &= Q(entry__store_id__in=allowed_store_ids)
        if entry_id:
            item_filter &= Q(entry_id=entry_id)
        if supplier_id:
            item_filter &= Q(entry__supplier_id=supplier_id)
        if user_id:
            item_filter &= Q(entry__created_by_id=user_id)
        if product_id:
            item_filter &= Q(product_id=product_id)
        if sku and sku.strip():
            item_filter &= Q(product__sku__iexact=sku.strip())
        if barcode and barcode.strip():
            item_filter &= Q(product__barcode__iexact=barcode.strip())
        if category_id:
            item_filter &= Q(product__category_id=category_id)
        if brand_id:
            item_filter &= Q(product__brand_id=brand_id)
        if has_returns is not None:
            has_ret_q = Exists(StockEntryReturnItem.objects.filter(entry_item_id=OuterRef("pk")))
            if has_returns is True or str(has_returns).lower() in ("true", "1", "yes"):
                item_filter &= Q(has_ret_q)
            elif has_returns is False or str(has_returns).lower() in ("false", "0", "no"):
                item_filter &= ~Q(has_ret_q)
        if search and search.strip():
            s = search.strip()
            search_q = (
                Q(product__name__icontains=s)
                | Q(product__sku__icontains=s)
                | Q(product__barcode__icontains=s)
                | Q(entry__supplier__name__icontains=s)
                | Q(entry__note__icontains=s)
            )
            if s.isdigit():
                search_q |= Q(entry_id=int(s))
            item_filter &= search_q

        # Query 1: Asosiy StockEntryItem so'rovi (barcha bog'liq modellar bilan bitta JOIN)
        items = list(
            StockEntryItem.objects
            .filter(item_filter)
            .select_related(
                "entry",
                "entry__store",
                "entry__supplier",
                "entry__created_by",
                "product",
                "product__category",
                "product__brand",
                "product__unit_measurement",
            )
            .order_by("-entry__created_at", "-entry__id", "id")
        )
        if not items:
            return [], empty_totals

        item_ids = [it.id for it in items]
        entry_ids = list({it.entry_id for it in items})

        # Query 2: Qaytimlar agregatsiyasi (alohida helper orqali)
        returns_map = cls.get_stock_entry_returns_map(item_ids)

        # Query 3: Yetkazib beruvchi tranzaksiyalari / to'lovlar agregatsiyasi (alohida helper orqali)
        tx_map = cls.get_stock_entry_transactions_map(entry_ids)

        product_status_map = {
            Product.ProductStatus.ACTIVE: "Faol",
            Product.ProductStatus.INACTIVE: "Nofaol (Arxiv)",
            Product.ProductStatus.DRAFT: "Qoralama",
        }

        payment_status_filter = payment_status.strip().lower() if payment_status else None

        rows = []
        unique_entry_ids = set()
        total_received_qty = Decimal("0")
        total_purchase_value = Decimal("0.00")
        total_sale_value = Decimal("0.00")
        total_potential_margin = Decimal("0.00")
        total_returned_qty = Decimal("0")
        total_returned_value = Decimal("0.00")
        total_net_purchase_value = Decimal("0.00")
        has_zero_cost = False

        for it in items:
            entry = it.entry
            prod = it.product
            qty = it.quantity or Decimal("0")
            unit_purchase_price = it.purchase_price or Decimal("0.00")
            unit_sale_price = it.selling_price or Decimal("0.00")
            unit_wholesale_price = it.wholesale_price or Decimal("0.00")

            purchase_value = (qty * unit_purchase_price).quantize(Decimal("0.01"))
            sale_value = (qty * unit_sale_price).quantize(Decimal("0.01"))
            potential_margin = (sale_value - purchase_value).quantize(Decimal("0.01"))

            ret_info = returns_map.get(it.id, {})
            ret_qty = ret_info.get("returned_qty") or Decimal("0")
            ret_val = ret_info.get("returned_value") or Decimal("0.00")
            net_qty = max(Decimal("0"), qty - ret_qty)
            net_purchase_val = max(Decimal("0.00"), (purchase_value - ret_val).quantize(Decimal("0.01")))

            # To'lov holati hisob-kitobi (ajratilgan helper orqali)
            p_status = cls.calculate_stock_entry_payment_status(entry, tx_map.get(entry.id))

            if payment_status_filter and p_status != payment_status_filter:
                continue

            # Bir martalik (single-pass) totals jamlash
            unique_entry_ids.add(entry.id)
            total_received_qty += qty
            total_purchase_value += purchase_value
            total_sale_value += sale_value
            total_potential_margin += potential_margin
            total_returned_qty += ret_qty
            total_returned_value += ret_val
            total_net_purchase_value += net_purchase_val
            if unit_purchase_price == 0:
                has_zero_cost = True

            created_by = entry.created_by
            created_by_name = (created_by.full_name or created_by.phone_number or created_by.username) if created_by else "-"
            status_display = product_status_map.get(prod.status, prod.get_status_display() or prod.status)
            unit_name = prod.unit_measurement.measurement if prod.unit_measurement else "dona"

            rows.append({
                "entry_id": entry.id,
                "store_id": entry.store_id,
                "store_name": entry.store.name if entry.store else "-",
                "entry_datetime": entry.created_at.strftime("%d.%m.%Y %H:%M") if entry.created_at else "-",
                "entry_timestamp": entry.created_at.isoformat() if entry.created_at else "",
                "supplier_id": entry.supplier_id,
                "supplier_name": entry.supplier.name if entry.supplier else "-",
                "created_by_id": entry.created_by_id,
                "created_by_name": created_by_name,
                "product_id": prod.id,
                "product_name": prod.name,
                "sku": prod.sku or "-",
                "barcode": prod.barcode or "-",
                "category_id": prod.category_id,
                "category_name": prod.category.name if prod.category else "-",
                "brand_id": prod.brand_id,
                "brand_name": prod.brand.name if prod.brand else "-",
                "unit": unit_name,
                "product_status": status_display,
                "quantity": float(qty),
                "unit_purchase_price": float(unit_purchase_price),
                "unit_sale_price": float(unit_sale_price),
                "unit_wholesale_price": float(unit_wholesale_price),
                "purchase_value": float(purchase_value),
                "sale_value": float(sale_value),
                "potential_margin": float(potential_margin),
                "returned_qty": float(ret_qty),
                "returned_value": float(ret_val),
                "net_quantity": float(net_qty),
                "net_purchase_value": float(net_purchase_val),
                "payment_status": p_status,
                "comment": entry.note or "",
            })

        if not rows:
            return [], empty_totals

        totals = {
            "total_entries_count": len(unique_entry_ids),
            "total_rows": len(rows),
            "total_received_qty": float(total_received_qty),
            "total_purchase_value": float(total_purchase_value),
            "total_sale_value": float(total_sale_value),
            "total_potential_margin": float(total_potential_margin),
            "total_returned_qty": float(total_returned_qty),
            "total_returned_value": float(total_returned_value),
            "total_net_purchase_value": float(total_net_purchase_value),
            "has_zero_cost_items": has_zero_cost,
        }

        return rows, totals


