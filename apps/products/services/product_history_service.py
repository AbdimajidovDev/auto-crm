"""
Mahsulot tarixi (Product History) servisi.

Bu servis FAQAT mahsulotning o'ziga bevosita tegishli bo'lgan o'zgarishlar tarixini taqdim etadi:
  1. field_change  → Mahsulot ma'lumotlari o'zgarganda (ProductFieldHistory);
  2. import        → Mahsulot qoldig'i qo'lda oshirilganda (+qty, StockAdjustment);
  3. writeoff      → Mahsulot qoldig'i hisobdan chiqarilganda (-qty, StockAdjustment);
  4. adjustment    → Mahsulot qoldig'i qayta sanalganda/to'g'irlanganda (StockAdjustment);
  5. inventory     → Inventarizatsiya natijasidagi miqdoriy tafovutlar (InventoryAdjustment);
  6. cancelled     → Bekor qilingan import/hisobdan chiqarish operatsiyalari (StockAdjustment status=cancelled).

Sale, SaleReturn va StockTransfer harakatlari ushbu servisga KIRMAYDI — ular o'zlarining alohida modullarida saqlanadi.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.common.store_scope import allowed_store_ids
from apps.inventory.models import InventoryAdjustment, StockAdjustment
from apps.products.models import ProductBatch, ProductFieldHistory
from apps.store.models import Store

ZERO = Decimal("0")
MONEY = DecimalField(max_digits=20, decimal_places=2)
MAX_EVENTS_WINDOW = 2000


def _amount(qty_field: str, price_field: str):
    return Coalesce(Sum(F(qty_field) * F(price_field), output_field=MONEY), ZERO, output_field=MONEY)


def _qty(field: str = "quantity"):
    return Coalesce(Sum(field), 0, output_field=DecimalField(max_digits=12, decimal_places=2))


def parse_date_param(value: str | None, *, end_of_day: bool = False):
    """'YYYY-MM-DD' → aware datetime; noto'g'ri/bo'sh qiymat → None."""
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    moment = datetime.combine(parsed.date(), time.max if end_of_day else time.min)
    return timezone.make_aware(moment, timezone.get_current_timezone())


class ProductHistoryService:
    """Mahsulot master-data va stock adjustmentlari tarixi servisi."""

    def __init__(self, product, user, *, date_from=None, date_to=None, store_id=None):
        self.product = product
        self.user = user
        self.date_from = date_from
        self.date_to = date_to
        self.store_id = int(store_id) if str(store_id or "").isdigit() else None

        allowed = allowed_store_ids(user)
        self.allowed_store_ids = allowed
        if allowed is not None and self.store_id is not None and self.store_id not in allowed:
            self.allowed_store_ids = set()

        stores = Store.objects.only("id", "name", "name_uz", "name_uz_cyrl").order_by("name")
        if self.allowed_store_ids is not None:
            stores = stores.filter(id__in=self.allowed_store_ids)
        self.stores = list(stores)
        self.store_names = {s.id: s.name for s in self.stores}

    def _apply_common(self, qs, *, date_field: str, store_path: str | None):
        qs = qs.filter(product=self.product)
        if self.date_from:
            qs = qs.filter(**{f"{date_field}__gte": self.date_from})
        if self.date_to:
            qs = qs.filter(**{f"{date_field}__lte": self.date_to})
        if store_path:
            if self.allowed_store_ids is not None:
                qs = qs.filter(**{f"{store_path}__in": self.allowed_store_ids})
            if self.store_id is not None:
                qs = qs.filter(**{store_path: self.store_id})
        return qs

    def stock_adjustments(self):
        qs = StockAdjustment.objects.filter(product=self.product)
        if self.date_from:
            qs = qs.filter(created_at__gte=self.date_from)
        if self.date_to:
            qs = qs.filter(created_at__lte=self.date_to)
        if self.allowed_store_ids is not None:
            qs = qs.filter(store_id__in=self.allowed_store_ids)
        if self.store_id is not None:
            qs = qs.filter(store_id=self.store_id)
        return qs

    def inventory_adjustments(self):
        qs = InventoryAdjustment.objects.filter(product=self.product)
        if self.date_from:
            qs = qs.filter(created_at__gte=self.date_from)
        if self.date_to:
            qs = qs.filter(created_at__lte=self.date_to)
        if self.allowed_store_ids is not None:
            qs = qs.filter(session__store_id__in=self.allowed_store_ids)
        if self.store_id is not None:
            qs = qs.filter(session__store_id=self.store_id)
        return qs

    def field_histories(self):
        qs = ProductFieldHistory.objects.filter(product=self.product)
        if self.date_from:
            qs = qs.filter(created_at__gte=self.date_from)
        if self.date_to:
            qs = qs.filter(created_at__lte=self.date_to)
        return qs

    def current_stock(self):
        qs = ProductBatch.objects.filter(product=self.product, is_active=True)
        if self.allowed_store_ids is not None:
            qs = qs.filter(store_id__in=self.allowed_store_ids)
        if self.store_id is not None:
            qs = qs.filter(store_id=self.store_id)
        return {
            row["store_id"]: row["qty"]
            for row in qs.order_by().values("store_id").annotate(qty=_qty())
        }

    def build_by_store(self):
        rows: dict[int, dict] = {}

        def row(store_id):
            if store_id is None:
                return None
            if store_id not in rows:
                rows[store_id] = {
                    "store_id": store_id,
                    "store_name": self.store_names.get(store_id, f"#{store_id}"),
                    "imported_qty": Decimal("0"),
                    "imported_amount": ZERO,
                    "written_off_qty": Decimal("0"),
                    "adjustments_count": 0,
                    "current_qty": Decimal("0"),
                }
            return rows[store_id]

        if self.store_id is None:
            for store in self.stores:
                row(store.id)
        else:
            row(self.store_id)

        # StockAdjustment importlari (faol)
        for item in (
            self.stock_adjustments()
            .filter(type=StockAdjustment.Type.IMPORT, status=StockAdjustment.Status.ACTIVE)
            .order_by()
            .values("store_id")
            .annotate(qty=_qty(), amount=Coalesce(Sum("total_amount"), ZERO, output_field=MONEY))
        ):
            target = row(item["store_id"])
            if target:
                target["imported_qty"] += item["qty"]
                target["imported_amount"] += item["amount"]

        # StockAdjustment hisobdan chiqarishlari (faol)
        for item in (
            self.stock_adjustments()
            .filter(type=StockAdjustment.Type.WRITE_OFF, status=StockAdjustment.Status.ACTIVE)
            .order_by()
            .values("store_id")
            .annotate(qty=_qty())
        ):
            target = row(item["store_id"])
            if target:
                target["written_off_qty"] += item["qty"]

        # StockAdjustment tuzatishlari soni
        for item in (
            self.stock_adjustments()
            .order_by()
            .values("store_id")
            .annotate(cnt=Count("id"))
        ):
            target = row(item["store_id"])
            if target:
                target["adjustments_count"] = item["cnt"]

        # Joriy qoldiq
        for store_id, qty in self.current_stock().items():
            target = row(store_id)
            if target:
                target["current_qty"] = qty

        result = list(rows.values())
        result.sort(key=lambda r: (-r["current_qty"], -r["imported_qty"], r["store_name"]))
        return result

    def build_summary(self, by_store):
        totals = {
            "current_qty": sum(r["current_qty"] for r in by_store),
            "imported_qty": sum(r["imported_qty"] for r in by_store),
            "imported_amount": sum((r["imported_amount"] for r in by_store), ZERO),
            "written_off_qty": sum(r["written_off_qty"] for r in by_store),
            "adjustments_count": sum(r["adjustments_count"] for r in by_store) or self.stock_adjustments().count(),
            "field_changes_count": self.field_histories().count(),
        }
        return totals

    def _event_sources(self, event_type: str | None):
        sources = {
            "field_change": (self._field_change_events, self.field_histories),
            "import": (
                lambda take: self._stock_adjustment_events(take, type_filter=StockAdjustment.Type.IMPORT),
                lambda: self.stock_adjustments().filter(type=StockAdjustment.Type.IMPORT),
            ),
            "writeoff": (
                lambda take: self._stock_adjustment_events(take, type_filter=StockAdjustment.Type.WRITE_OFF),
                lambda: self.stock_adjustments().filter(type=StockAdjustment.Type.WRITE_OFF),
            ),
            "adjustment": (
                lambda take: self._stock_adjustment_events(
                    take, exclude_types=[StockAdjustment.Type.IMPORT, StockAdjustment.Type.WRITE_OFF]
                ),
                lambda: self.stock_adjustments().exclude(
                    type__in=[StockAdjustment.Type.IMPORT, StockAdjustment.Type.WRITE_OFF]
                ),
            ),
            "inventory": (self._inventory_events, self.inventory_adjustments),
        }
        if event_type and event_type in sources:
            return {event_type: sources[event_type]}
        return sources

    def _field_change_events(self, take: int):
        qs = (
            self.field_histories()
            .select_related("user")
            .order_by("-created_at", "-id")[:take]
        )
        events = []
        for fh in qs:
            events.append(
                {
                    "type": "field_change",
                    "date": fh.created_at,
                    "doc_id": fh.id,
                    "store_id": None,
                    "store_name": None,
                    "field_name": fh.field_name,
                    "field_label": fh.field_label,
                    "old_value": fh.old_value,
                    "new_value": fh.new_value,
                    "quantity": Decimal("0"),
                    "old_quantity": None,
                    "new_quantity": None,
                    "difference": None,
                    "price": None,
                    "sale_price": None,
                    "amount": None,
                    "counterparty": None,
                    "user": fh.user_display or (getattr(fh.user, "full_name", None) if fh.user else "Tizim"),
                    "status": "changed",
                    "reason": None,
                    "note": f"{fh.field_label}: {fh.old_value} → {fh.new_value}",
                }
            )
        return events

    def _stock_adjustment_events(
        self, take: int, type_filter: str | None = None, exclude_types: list | None = None
    ):
        qs = self.stock_adjustments()
        if type_filter:
            qs = qs.filter(type=type_filter)
        if exclude_types:
            qs = qs.exclude(type__in=exclude_types)
        qs = (
            qs.select_related("store", "created_by", "cancelled_by")
            .order_by("-created_at", "-id")[:take]
        )
        events = []
        reason_labels = dict(StockAdjustment.Reason.choices)

        for adj in qs:
            if adj.type == StockAdjustment.Type.IMPORT:
                ev_type = "import"
                qty = adj.quantity
            elif adj.type == StockAdjustment.Type.WRITE_OFF:
                ev_type = "writeoff"
                qty = -adj.quantity
            else:
                ev_type = "adjustment"
                qty = adj.difference

            reason_str = reason_labels.get(adj.reason, adj.reason) or adj.comment or ""
            note = adj.comment if adj.comment and adj.comment != reason_str else reason_str

            if adj.status == StockAdjustment.Status.CANCELLED:
                cancelled_by_name = (
                    getattr(adj.cancelled_by, "full_name", None)
                    or getattr(adj.cancelled_by, "phone_number", "")
                )
                cancel_info = f" [Bekor qilingan: {cancelled_by_name}]" if cancelled_by_name else " [Bekor qilingan]"
                note = f"{note}{cancel_info}".strip()

            events.append(
                {
                    "type": ev_type,
                    "date": adj.created_at,
                    "doc_id": adj.id,
                    "store_id": adj.store_id,
                    "store_name": adj.store.name if adj.store else f"#{adj.store_id}",
                    "quantity": qty,
                    "old_quantity": adj.old_quantity,
                    "new_quantity": adj.new_quantity,
                    "difference": adj.difference,
                    "price": adj.purchase_price,
                    "sale_price": adj.sale_price,
                    "amount": adj.total_amount,
                    "counterparty": None,
                    "user": (
                        getattr(adj.created_by, "full_name", None)
                        or getattr(adj.created_by, "phone_number", None)
                    ),
                    "status": adj.status,
                    "cancelled_by": getattr(adj.cancelled_by, "full_name", None),
                    "cancelled_at": adj.cancelled_at,
                    "reason": adj.reason,
                    "note": note,
                }
            )
        return events

    def _inventory_events(self, take: int):
        qs = (
            self.inventory_adjustments()
            .select_related("session", "session__store", "session__started_by")
            .order_by("-created_at", "-id")[:take]
        )
        events = []
        for adjustment in qs:
            session = adjustment.session
            events.append(
                {
                    "type": "inventory",
                    "date": adjustment.created_at,
                    "doc_id": session.id,
                    "store_id": session.store_id,
                    "store_name": session.store.name if session.store else f"#{session.store_id}",
                    "quantity": adjustment.difference,
                    "old_quantity": None,
                    "new_quantity": None,
                    "difference": adjustment.difference,
                    "price": None,
                    "sale_price": None,
                    "amount": None,
                    "counterparty": None,
                    "user": getattr(session.started_by, "full_name", None),
                    "status": session.status,
                    "reason": None,
                    "note": f"Inventarizatsiya #{session.id} tafovuti: {adjustment.difference}",
                }
            )
        return events

    def _merged_events(self, *, take: int, event_type: str | None = None):
        events = []
        for _, (build, _queryset) in self._event_sources(event_type).items():
            events.extend(build(take))
        events.sort(key=lambda e: e["date"], reverse=True)
        return events

    def collect_events(self, *, limit: int = MAX_EVENTS_WINDOW, event_type: str | None = None):
        return self._merged_events(take=limit, event_type=event_type)[:limit]

    def count_events(self, event_type: str | None = None) -> int:
        return sum(
            queryset().count()
            for _, (_build, queryset) in self._event_sources(event_type).items()
        )

    def build_events(self, *, page: int = 1, limit: int = 50, event_type: str | None = None):
        page = max(1, page)
        limit = max(1, min(limit, 200))
        offset = (page - 1) * limit
        take = min(offset + limit, MAX_EVENTS_WINDOW)

        events = self._merged_events(take=take, event_type=event_type)
        total = self.count_events(event_type)

        window = events[offset : offset + limit]
        return {
            "count": total,
            "page": page,
            "limit": limit,
            "truncated": total > MAX_EVENTS_WINDOW,
            "results": window,
        }

    def build(self, *, page: int = 1, limit: int = 50, event_type: str | None = None):
        by_store = self.build_by_store()
        return {
            "summary": self.build_summary(by_store),
            "by_store": by_store,
            "events": self.build_events(page=page, limit=limit, event_type=event_type),
            "stores": [{"id": s.id, "name": s.name} for s in self.stores],
        }
