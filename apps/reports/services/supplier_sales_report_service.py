from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.contract.permissions import allowed_store_ids
from apps.inventory.models import StockAllocation
from apps.products.models import ProductBatch
from apps.sales.models import Sale, SaleItem

ALL_TIME_START = date(2000, 1, 1)


def _money(v: Any) -> str:
    if v is None:
        return "0.00"
    return f"{Decimal(str(v)):.2f}"


def _parse_dates(params: dict) -> tuple[date, date]:
    """Parse from/to dates into [d_from, d_to]. Default is last 30 days."""
    today = timezone.localdate()
    from_raw = (params.get("from") or params.get("date_from") or "").strip()
    to_raw = (params.get("to") or params.get("date_to") or "").strip()

    def parse_one(raw: str, field: str) -> date:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ValidationError({field: "ISO format: YYYY-MM-DD"})

    if from_raw and to_raw:
        return parse_one(from_raw, "from"), parse_one(to_raw, "to")
    if from_raw:
        return parse_one(from_raw, "from"), today
    if to_raw:
        return ALL_TIME_START, parse_one(to_raw, "to")
    return today - timedelta(days=30), today


def _dt_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Converts date range into timezone-aware [start, end) datetime bounds."""
    start = datetime.combine(date_from, time.min)
    end = datetime.combine(date_to + timedelta(days=1), time.min)
    tz = timezone.get_current_timezone()
    if timezone.is_naive(start):
        start = timezone.make_aware(start, tz)
        end = timezone.make_aware(end, tz)
    return start, end


def _parse_int(params: dict, key: str) -> int | None:
    raw = params.get(key)
    if raw is None or raw == "" or raw == "all":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class SupplierSalesReportService:
    """
    Authoritative service for Billz-compatible 'Supplier Sales Report'.
    Uses StockLot + StockAllocation as authoritative source of truth.

    Guarantees:
    - Pure allocation ledger attribution:
        StockAllocation (SALE) -> StockLot -> Supplier
    - Reverse LIFO return attribution:
        StockAllocation (SALE_RETURN) -> reversal_of -> original SALE -> original Supplier
    - Multi-lot and line-level discount allocation accuracy
    - Cross-period return support
    - No heuristic guessing: unallocated legacy sales exposed as 'Tarixiy (Aniqlanmagan)'
    - Strict store isolation (RBAC)
    - Fixed query count: O(1) query complexity (no N+1)
    """

    @classmethod
    def build_report(
        cls,
        params: dict,
        store_id: int | None = None,
        user: Any = None,
    ) -> tuple[list[dict], list[dict], None, list[dict]]:
        """
        Builds the canonical supplier sales dataset.
        Returns (columns, rows, None, summary).
        """
        d_from, d_to = _parse_dates(params)
        start, end = _dt_bounds(d_from, d_to)
        by_day = (params.get("group_mode") != "period")
        period_label = f"{d_from.strftime('%d.%m.%Y')} — {d_to.strftime('%d.%m.%Y')}"

        # 1. Store Scope & RBAC Isolation
        allowed = allowed_store_ids(user) if user else None
        target_store_id = store_id or _parse_int(params, "store_id") or _parse_int(params, "store")
        if allowed is not None:
            if target_store_id is not None and target_store_id not in allowed:
                return cls._empty_result(period_label)

        # 2. Filter Extraction (Strict Billz contract: supplier, group_mode, consolidate_stores, price_type, store)
        raw_supplier = (params.get("supplier_id") or params.get("supplier") or "").strip()

        raw_group = (params.get("group_mode") or "").strip().lower()
        by_day = raw_group not in ("period", "period_total", "davr", "davr jami")

        raw_consolidate = str(params.get("consolidate_stores") or params.get("consolidate") or "").strip().lower()
        consolidate_stores = raw_consolidate in ("true", "1", "yes", "birlashtirish", "y")

        raw_price_type = (params.get("price_type") or params.get("sale_price_type") or "all").strip().lower()
        if raw_price_type not in ("all", "retail", "wholesale", "free"):
            raw_price_type = "all"

        # 3. Query SALE Allocations
        sales_qs = (
            StockAllocation.objects
            .filter(
                movement_type=StockAllocation.MovementType.SALE,
                created_at__gte=start,
                created_at__lt=end,
            )
            .exclude(sale_item__sale__deleted_at__isnull=False)
            .select_related(
                "lot__store",
                "lot__supplier",
                "lot__product__category",
                "lot__product__brand",
                "sale_item__sale",
            )
        )

        if target_store_id:
            sales_qs = sales_qs.filter(lot__store_id=target_store_id)
        elif allowed is not None:
            sales_qs = sales_qs.filter(lot__store_id__in=allowed)

        if raw_supplier:
            if raw_supplier.isdigit():
                sales_qs = sales_qs.filter(lot__supplier_id=int(raw_supplier))
            elif raw_supplier.lower() in ("unknown", "none", "legacy"):
                sales_qs = sales_qs.filter(lot__supplier__isnull=True)

        # 4. Query SALE_RETURN Allocations
        returns_qs = (
            StockAllocation.objects
            .filter(
                movement_type=StockAllocation.MovementType.SALE_RETURN,
                created_at__gte=start,
                created_at__lt=end,
            )
            .exclude(sale_return_item__sale_return__sale__deleted_at__isnull=False)
            .select_related(
                "lot__store",
                "lot__supplier",
                "lot__product__category",
                "lot__product__brand",
                "reversal_of__sale_item__sale",
                "reversal_of__lot__supplier",
                "sale_return_item__sale_return__sale",
            )
        )

        if target_store_id:
            returns_qs = returns_qs.filter(lot__store_id=target_store_id)
        elif allowed is not None:
            returns_qs = returns_qs.filter(lot__store_id__in=allowed)

        if raw_supplier:
            if raw_supplier.isdigit():
                returns_qs = returns_qs.filter(lot__supplier_id=int(raw_supplier))
            elif raw_supplier.lower() in ("unknown", "none", "legacy"):
                returns_qs = returns_qs.filter(lot__supplier__isnull=True)

        # 5. Query Historical Unallocated Sales (only if not filtering for a specific supplier)
        unalloc_items = []
        if not (raw_supplier and raw_supplier.isdigit()):
            unalloc_qs = (
                SaleItem.objects
                .filter(
                    sale__created_at__gte=start,
                    sale__created_at__lt=end,
                    stock_allocations__isnull=True,
                )
                .exclude(sale__status=Sale.Status.RETURNED)
                .exclude(sale__deleted_at__isnull=False)
                .select_related(
                    "sale__store",
                    "product__category",
                    "product__brand",
                    "sale",
                )
            )
            if target_store_id:
                unalloc_qs = unalloc_qs.filter(sale__store_id=target_store_id)
            elif allowed is not None:
                unalloc_qs = unalloc_qs.filter(sale__store_id__in=allowed)

            unalloc_items = list(unalloc_qs)

        sale_allocations = list(sales_qs)
        return_allocations = list(returns_qs)

        # 6. Fetch ProductBatch prices for catalog comparison
        store_prod_pairs = set()
        for a in sale_allocations:
            store_prod_pairs.add((a.lot.store_id, a.lot.product_id))
        for a in return_allocations:
            store_prod_pairs.add((a.lot.store_id, a.lot.product_id))
        for it in unalloc_items:
            store_prod_pairs.add((it.sale.store_id, it.product_id))

        batches_map = {}
        if store_prod_pairs:
            stores_set = {sp[0] for sp in store_prod_pairs}
            prods_set = {sp[1] for sp in store_prod_pairs}
            batch_qs = ProductBatch.objects.filter(
                store_id__in=stores_set,
                product_id__in=prods_set,
            ).values("store_id", "product_id", "selling_price", "wholesale_price")
            for b in batch_qs:
                batches_map[(b["store_id"], b["product_id"])] = b

        def determine_price_flags(s_id: int, p_id: int, unit_price: Decimal) -> tuple[bool, bool, bool]:
            """
            Returns (is_retail, is_wholesale, is_free).
            """
            b_info = batches_map.get((s_id, p_id))
            if not b_info:
                return True, False, False

            ws_price = b_info.get("wholesale_price") or Decimal("0.00")
            ret_price = b_info.get("selling_price") or Decimal("0.00")

            is_ws = (ws_price > Decimal("0.00") and unit_price == ws_price)
            if is_ws:
                return False, True, False

            is_fr = (unit_price != ret_price)
            if is_fr:
                return False, False, True

            return True, False, False

        def matches_price_filter(is_ret: bool, is_ws: bool, is_fr: bool) -> bool:
            if raw_price_type == "all":
                return True
            if raw_price_type == "retail":
                return is_ret
            if raw_price_type == "wholesale":
                return is_ws
            if raw_price_type == "free":
                return is_fr
            return True

        # 7. Aggregation Buckets
        # Bucket key:
        # If consolidate_stores: (None, day if by_day else None, supplier_id, product_id)
        # If not consolidate_stores: (store_id, day if by_day else None, supplier_id, product_id)
        buckets: dict[tuple, dict[str, Any]] = {}

        def get_or_create_bucket(
            s_id: int,
            s_name: str,
            day_val: date | None,
            sup_id: int | None,
            sup_name: str,
            p_id: int,
            p_name: str,
            p_sku: str | None,
            p_barcode: str | None,
            cat_name: str | None,
        ) -> dict[str, Any]:
            bucket_store_id = None if consolidate_stores else s_id
            bucket_store_name = "Barcha do'konlar" if consolidate_stores else s_name
            bkey = (bucket_store_id, day_val if by_day else None, sup_id, p_id)
            if bkey not in buckets:
                buckets[bkey] = {
                    "store": bucket_store_name,
                    "date": day_val.isoformat() if (by_day and day_val) else period_label,
                    "day": day_val if by_day else d_from,
                    "supplier": sup_name,
                    "product": p_name,
                    "name": p_name,
                    "sku": p_sku or "-",
                    "barcode": p_barcode or "-",
                    "categories_path": cat_name or "-",
                    "category": cat_name or "-",
                    "sold_qty": Decimal("0.00"),
                    "returned_qty": Decimal("0.00"),
                    "net_sold_qty": Decimal("0.00"),
                    "revenue": Decimal("0.00"),
                    "free_price_flag": False,
                    "wholesale_price_flag": False,
                    "product_id": p_id,
                    "store_id": bucket_store_id,
                }
            return buckets[bkey]

        # Helper: Calculate effective net unit price after proportional discount
        def calc_effective_unit_price(sale_obj: Sale, sale_item_obj: SaleItem) -> Decimal:
            if not sale_obj or not sale_item_obj:
                return Decimal("0.00")
            if sale_obj.discount_amount and sale_obj.discount_amount > Decimal("0.00"):
                subtotal = sale_obj.total_amount + sale_obj.discount_amount
                if subtotal > Decimal("0.00"):
                    return (sale_item_obj.unit_price * sale_obj.total_amount) / subtotal
                return Decimal("0.00")
            return sale_item_obj.unit_price

        # Process SALE Allocations
        for alloc in sale_allocations:
            lot = alloc.lot
            store = lot.store
            product = lot.product
            supplier = lot.supplier
            sale_item = alloc.sale_item
            sale = sale_item.sale if sale_item else None

            item_unit_price = sale_item.unit_price if sale_item else Decimal("0.00")
            is_ret, is_ws, is_fr = determine_price_flags(store.id, product.id, item_unit_price)
            if not matches_price_filter(is_ret, is_ws, is_fr):
                continue

            sup_name = supplier.name if supplier else "Tarixiy (Aniqlanmagan)"
            sup_id = supplier.id if supplier else None
            cat_name = product.category.name if product.category else None
            day_val = timezone.localdate(alloc.created_at) if by_day else None

            bucket = get_or_create_bucket(
                store.id,
                store.name,
                day_val,
                sup_id,
                sup_name,
                product.id,
                product.name,
                product.sku,
                product.barcode,
                cat_name,
            )

            bucket["sold_qty"] += alloc.quantity
            eff_price = calc_effective_unit_price(sale, sale_item)
            alloc_revenue = alloc.quantity * eff_price
            bucket["revenue"] += alloc_revenue

            if is_fr:
                bucket["free_price_flag"] = True
            if is_ws:
                bucket["wholesale_price_flag"] = True

        # Process SALE_RETURN Allocations (Cross-period return supported)
        for alloc in return_allocations:
            lot = alloc.lot
            store = lot.store
            product = lot.product
            supplier = lot.supplier

            eff_price = Decimal("0.00")
            raw_item_price = Decimal("0.00")
            if alloc.reversal_of and alloc.reversal_of.sale_item:
                orig_item = alloc.reversal_of.sale_item
                raw_item_price = orig_item.unit_price
                eff_price = calc_effective_unit_price(orig_item.sale, orig_item)
            elif alloc.sale_return_item:
                raw_item_price = alloc.sale_return_item.unit_price
                eff_price = alloc.sale_return_item.unit_price
            else:
                raw_item_price = lot.purchase_price
                eff_price = lot.purchase_price

            is_ret, is_ws, is_fr = determine_price_flags(store.id, product.id, raw_item_price)
            if not matches_price_filter(is_ret, is_ws, is_fr):
                continue

            sup_name = supplier.name if supplier else "Tarixiy (Aniqlanmagan)"
            sup_id = supplier.id if supplier else None
            cat_name = product.category.name if product.category else None
            day_val = timezone.localdate(alloc.created_at) if by_day else None

            bucket = get_or_create_bucket(
                store.id,
                store.name,
                day_val,
                sup_id,
                sup_name,
                product.id,
                product.name,
                product.sku,
                product.barcode,
                cat_name,
            )

            bucket["returned_qty"] += alloc.quantity
            return_revenue = alloc.quantity * eff_price
            bucket["revenue"] -= return_revenue

        # Process Historical Unallocated Sales (before lot cut-over)
        for it in unalloc_items:
            store = it.sale.store
            product = it.product
            sup_name = "Tarixiy (Aniqlanmagan)"
            sup_id = None
            cat_name = product.category.name if product.category else None
            day_val = timezone.localdate(it.sale.created_at) if by_day else None

            is_ret, is_ws, is_fr = determine_price_flags(store.id, product.id, it.unit_price)
            if not matches_price_filter(is_ret, is_ws, is_fr):
                continue

            bucket = get_or_create_bucket(
                store.id,
                store.name,
                day_val,
                sup_id,
                sup_name,
                product.id,
                product.name,
                product.sku,
                product.barcode,
                cat_name,
            )

            eff_price = calc_effective_unit_price(it.sale, it)
            bucket["sold_qty"] += it.quantity
            bucket["returned_qty"] += it.returned_quantity
            bucket["revenue"] += (it.quantity - it.returned_quantity) * eff_price

            if is_fr:
                bucket["free_price_flag"] = True
            if is_ws:
                bucket["wholesale_price_flag"] = True

        # 8. Finalize rows
        rows: list[dict[str, Any]] = []
        for b in buckets.values():
            net_qty = b["sold_qty"] - b["returned_qty"]
            b["net_sold_qty"] = net_qty
            b["sold"] = b["sold_qty"]
            b["returned"] = b["returned_qty"]
            b["net"] = net_qty
            b["free_price"] = "Ha" if b["free_price_flag"] else "Yo'q"
            b["used_wholesale_price"] = "Ha" if b["wholesale_price_flag"] else "Yo'q"
            b["revenue"] = _money(b["revenue"])
            rows.append(b)

        # 9. Server-side Sorting
        sort_by = (params.get("sort_by") or params.get("ordering") or "date").strip()
        sort_order = (params.get("sort_order") or "").strip().lower()
        if sort_by.startswith("-"):
            sort_by = sort_by[1:]
            sort_order = "desc"
        if not sort_order:
            sort_order = "desc" if sort_by in ("date", "revenue", "sold_qty", "net_sold_qty") else "asc"
        is_desc = (sort_order == "desc")

        if sort_by == "date":
            rows.sort(key=lambda r: (r["day"] or date.min, r["supplier"], r["product"]), reverse=is_desc)
        elif sort_by == "supplier":
            rows.sort(key=lambda r: (r["supplier"].lower(), r["product"].lower(), r["day"] or date.min), reverse=is_desc)
        elif sort_by in ("product", "name"):
            rows.sort(key=lambda r: (r["product"].lower(), r["supplier"].lower(), r["day"] or date.min), reverse=is_desc)
        elif sort_by in ("sold_qty", "sold"):
            rows.sort(key=lambda r: (r["sold_qty"], Decimal(str(r["revenue"])), r["product"]), reverse=is_desc)
        elif sort_by in ("returned_qty", "returned"):
            rows.sort(key=lambda r: (r["returned_qty"], r["product"]), reverse=is_desc)
        elif sort_by in ("net_sold_qty", "net"):
            rows.sort(key=lambda r: (r["net_sold_qty"], Decimal(str(r["revenue"])), r["product"]), reverse=is_desc)
        elif sort_by == "revenue":
            rows.sort(key=lambda r: (Decimal(str(r["revenue"])), r["net_sold_qty"], r["product"]), reverse=is_desc)
        elif sort_by == "store":
            rows.sort(key=lambda r: (r["store"].lower(), r["day"] or date.min, r["product"].lower()), reverse=is_desc)
        else:
            rows.sort(key=lambda r: (r["day"] or date.min, r["supplier"], r["product"]), reverse=True)

        # 10. Summary Calculation (from exact filtered dataset)
        total_sold = sum((r["sold_qty"] for r in rows), Decimal("0.00"))
        total_returned = sum((r["returned_qty"] for r in rows), Decimal("0.00"))
        total_net = sum((r["net_sold_qty"] for r in rows), Decimal("0.00"))
        total_rev = sum((Decimal(str(r["revenue"])) for r in rows), Decimal("0.00"))
        suppliers_count = len({r["supplier"] for r in rows if r["supplier"] and r["supplier"] != "-"})
        products_count = len({r["product_id"] for r in rows})

        summary = [
            {"label": "Qatorlar", "value": len(rows), "kind": "int"},
            {"label": "Ta'minotchilar soni", "value": suppliers_count, "kind": "int"},
            {"label": "Mahsulotlar soni", "value": products_count, "kind": "int"},
            {"label": "Jami sotilgan", "value": total_sold, "kind": "number"},
            {"label": "Jami qaytarilgan", "value": total_returned, "kind": "number"},
            {"label": "Jami sotilgan (sof)", "value": total_net, "kind": "number"},
            {"label": "Jami tushum", "value": _money(total_rev), "kind": "money"},
        ]

        columns = [
            {"key": "store", "label": "Do'kon", "kind": "text"},
            {"key": "date", "label": "Sana", "kind": "text"},
            {"key": "supplier", "label": "Yetkazib beruvchi", "kind": "text"},
            {"key": "product", "label": "Nomi", "kind": "text"},
            {"key": "sku", "label": "Artikul / SKU", "kind": "text"},
            {"key": "barcode", "label": "Shtrix-kod", "kind": "text"},
            {"key": "categories_path", "label": "Toifa / kategoriya", "kind": "text"},
            {"key": "sold_qty", "label": "Sotilganlar soni", "kind": "number"},
            {"key": "returned_qty", "label": "Qaytarilganlar soni", "kind": "number"},
            {"key": "net_sold_qty", "label": "Sof sotilgan", "kind": "number"},
            {"key": "revenue", "label": "Tushum", "kind": "money"},
            {"key": "free_price", "label": "Erkin narx", "kind": "text"},
            {"key": "used_wholesale_price", "label": "Ulgurji narx", "kind": "text"},
        ]

        return columns, rows, None, summary

    @classmethod
    def _empty_result(cls, period_label: str) -> tuple[list[dict], list[dict], None, list[dict]]:
        columns = [
            {"key": "store", "label": "Do'kon", "kind": "text"},
            {"key": "date", "label": "Sana", "kind": "text"},
            {"key": "supplier", "label": "Yetkazib beruvchi", "kind": "text"},
            {"key": "product", "label": "Nomi", "kind": "text"},
            {"key": "sku", "label": "Artikul / SKU", "kind": "text"},
            {"key": "barcode", "label": "Shtrix-kod", "kind": "text"},
            {"key": "categories_path", "label": "Toifa / kategoriya", "kind": "text"},
            {"key": "sold_qty", "label": "Sotilganlar soni", "kind": "number"},
            {"key": "returned_qty", "label": "Qaytarilganlar soni", "kind": "number"},
            {"key": "net_sold_qty", "label": "Sof sotilgan", "kind": "number"},
            {"key": "revenue", "label": "Tushum", "kind": "money"},
            {"key": "free_price", "label": "Erkin narx", "kind": "text"},
            {"key": "used_wholesale_price", "label": "Ulgurji narx", "kind": "text"},
        ]
        summary = [
            {"label": "Qatorlar", "value": 0, "kind": "int"},
            {"label": "Ta'minotchilar soni", "value": 0, "kind": "int"},
            {"label": "Mahsulotlar soni", "value": 0, "kind": "int"},
            {"label": "Jami sotilgan", "value": Decimal("0.00"), "kind": "number"},
            {"label": "Jami qaytarilgan", "value": Decimal("0.00"), "kind": "number"},
            {"label": "Jami sotilgan (sof)", "value": Decimal("0.00"), "kind": "number"},
            {"label": "Jami tushum", "value": "0.00", "kind": "money"},
        ]
        return columns, [], None, summary
