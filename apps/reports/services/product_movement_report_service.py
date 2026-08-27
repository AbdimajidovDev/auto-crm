from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.common.store_scope import allowed_store_ids
from apps.contract.models import StockEntryItem, StockEntryReturnItem
from apps.inventory.models import InventoryAdjustment
from apps.products.models import ProductBatch
from apps.sales.models import SaleItem, SaleReturnItem
from apps.store.models import Store
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.writeoff.models import WriteOff, WriteOffItem

ZERO = Decimal("0")
MONEY = DecimalField(max_digits=20, decimal_places=2)


def _amount(qty_field: str, price_field: str):
    return Coalesce(Sum(F(qty_field) * F(price_field), output_field=MONEY), ZERO, output_field=MONEY)


def _qty(field: str = "quantity"):
    return Coalesce(Sum(field), 0, output_field=DecimalField(max_digits=12, decimal_places=2))


class ProductMovementReportService:
    """
    Hisobotlar moduli uchun mahsulot harakatlari tahliliy xizmati.
    Bu servis faqat analytics hisobotlari uchun bo'lib, mahsulot master-data tarixiga ta'sir qilmaydi.
    """

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

    def entry_items(self):
        return self._apply_common(
            StockEntryItem.objects.all(),
            date_field="entry__created_at",
            store_path="entry__store_id",
        )

    def entry_return_items(self):
        return self._apply_common(
            StockEntryReturnItem.objects.all(),
            date_field="stock_return__created_at",
            store_path="stock_return__entry__store_id",
        )

    def sale_items(self):
        qs = SaleItem.objects.filter(sale__deleted_at__isnull=True)
        return self._apply_common(qs, date_field="sale__created_at", store_path="sale__store_id")

    def sale_return_items(self):
        return self._apply_common(
            SaleReturnItem.objects.all(),
            date_field="sale_return__created_at",
            store_path="sale_return__store_id",
        )

    def writeoff_items(self):
        return self._apply_common(
            WriteOffItem.objects.all(),
            date_field="write_off__created_at",
            store_path="write_off__store_id",
        )

    def transfer_items(self):
        qs = StockTransferItem.objects.filter(product=self.product)
        if self.date_from:
            qs = qs.filter(stock_transfer__created_at__gte=self.date_from)
        if self.date_to:
            qs = qs.filter(stock_transfer__created_at__lte=self.date_to)
        if self.allowed_store_ids is not None:
            qs = qs.filter(
                Q(stock_transfer__from_store_id__in=self.allowed_store_ids)
                | Q(stock_transfer__to_store_id__in=self.allowed_store_ids)
            )
        if self.store_id is not None:
            qs = qs.filter(
                Q(stock_transfer__from_store_id=self.store_id)
                | Q(stock_transfer__to_store_id=self.store_id)
            )
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
                    "purchased_qty": 0,
                    "purchased_amount": ZERO,
                    "sold_qty": 0,
                    "sold_amount": ZERO,
                    "sale_returned_qty": 0,
                    "purchase_returned_qty": 0,
                    "transferred_in_qty": 0,
                    "transferred_out_qty": 0,
                    "written_off_qty": 0,
                    "current_qty": 0,
                }
            return rows[store_id]

        if self.store_id is None:
            for store in self.stores:
                row(store.id)
        else:
            row(self.store_id)

        for item in (
            self.entry_items()
            .order_by()
            .values("entry__store_id")
            .annotate(qty=_qty(), amount=_amount("quantity", "purchase_price"))
        ):
            target = row(item["entry__store_id"])
            if target:
                target["purchased_qty"] = item["qty"]
                target["purchased_amount"] = item["amount"]

        for item in (
            self.sale_items()
            .order_by()
            .values("sale__store_id")
            .annotate(qty=_qty(), amount=Coalesce(Sum("total_price"), ZERO, output_field=MONEY))
        ):
            target = row(item["sale__store_id"])
            if target:
                target["sold_qty"] = item["qty"]
                target["sold_amount"] = item["amount"]

        for item in (
            self.sale_return_items().order_by().values("sale_return__store_id").annotate(qty=_qty())
        ):
            target = row(item["sale_return__store_id"])
            if target:
                target["sale_returned_qty"] = item["qty"]

        for item in (
            self.entry_return_items()
            .order_by()
            .values("stock_return__entry__store_id")
            .annotate(qty=_qty())
        ):
            target = row(item["stock_return__entry__store_id"])
            if target:
                target["purchase_returned_qty"] = item["qty"]

        for item in (
            self.writeoff_items().order_by().values("write_off__store_id").annotate(qty=_qty())
        ):
            target = row(item["write_off__store_id"])
            if target:
                target["written_off_qty"] = item["qty"]

        approved_transfers = self.transfer_items().filter(
            stock_transfer__status=StockTransfer.Status.APPROVED
        )
        for item in (
            approved_transfers
            .order_by()
            .values("stock_transfer__from_store_id", "stock_transfer__to_store_id")
            .annotate(qty=_qty())
        ):
            out_row = row(item["stock_transfer__from_store_id"])
            if out_row:
                out_row["transferred_out_qty"] += item["qty"]
            in_row = row(item["stock_transfer__to_store_id"])
            if in_row:
                in_row["transferred_in_qty"] += item["qty"]

        for store_id, qty in self.current_stock().items():
            target = row(store_id)
            if target:
                target["current_qty"] = qty

        result = list(rows.values())
        result.sort(key=lambda r: (-r["current_qty"], -r["sold_qty"], r["store_name"]))
        return result

    def build_summary(self, by_store):
        totals = {
            "purchased_qty": sum(r["purchased_qty"] for r in by_store),
            "purchased_amount": sum((r["purchased_amount"] for r in by_store), ZERO),
            "sold_qty": sum(r["sold_qty"] for r in by_store),
            "sold_amount": sum((r["sold_amount"] for r in by_store), ZERO),
            "sale_returned_qty": sum(r["sale_returned_qty"] for r in by_store),
            "purchase_returned_qty": sum(r["purchase_returned_qty"] for r in by_store),
            "written_off_qty": sum(r["written_off_qty"] for r in by_store),
            "current_qty": sum(r["current_qty"] for r in by_store),
        }

        sale_agg = self.sale_items().aggregate(
            cost=Coalesce(
                Sum(F("quantity") * F("purchase_price"), output_field=MONEY), ZERO, output_field=MONEY
            ),
            costed_qty=Coalesce(
                Sum("quantity", filter=Q(purchase_price__isnull=False)), 0,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            sale_count=Count("id"),
        )
        returned_amount = self.sale_return_items().aggregate(
            amount=Coalesce(Sum("total_price"), ZERO, output_field=MONEY)
        )["amount"]

        totals["sale_returned_amount"] = returned_amount
        totals["net_sold_amount"] = totals["sold_amount"] - returned_amount
        totals["net_sold_qty"] = totals["sold_qty"] - totals["sale_returned_qty"]
        totals["cost_amount"] = sale_agg["cost"] or ZERO
        totals["profit"] = totals["sold_amount"] - totals["cost_amount"]
        totals["costed_qty"] = sale_agg["costed_qty"] or 0
        totals["profit_partial"] = totals["costed_qty"] < totals["sold_qty"]
        totals["sale_line_count"] = sale_agg["sale_count"] or 0

        transfers = self.transfer_items()
        totals["transferred_qty"] = transfers.aggregate(qty=_qty())["qty"]
        totals["transfer_count"] = transfers.order_by().values("stock_transfer_id").distinct().count()

        entries = self.entry_items()
        totals["entry_count"] = entries.order_by().values("entry_id").distinct().count()
        totals["supplier_count"] = (
            entries.order_by().values("entry__supplier_id").distinct().count()
        )

        totals["avg_purchase_price"] = (
            (totals["purchased_amount"] / totals["purchased_qty"]) if totals["purchased_qty"] else ZERO
        )
        totals["avg_selling_price"] = (
            (totals["sold_amount"] / totals["sold_qty"]) if totals["sold_qty"] else ZERO
        )

        first_entry = entries.order_by("entry__created_at").values_list("entry__created_at", flat=True).first()
        last_entry = entries.order_by("-entry__created_at").values_list("entry__created_at", flat=True).first()
        last_sale = (
            self.sale_items().order_by("-sale__created_at").values_list("sale__created_at", flat=True).first()
        )
        totals["first_entry_at"] = first_entry
        totals["last_entry_at"] = last_entry
        totals["last_sale_at"] = last_sale

        return totals

    def _event_sources(self, event_type: str | None):
        sources = {
            "entry": (self._entries_events, self.entry_items),
            "transfer": (self._transfer_events, self.transfer_items),
            "sale": (self._sale_events, self.sale_items),
            "sale_return": (self._sale_return_events, self.sale_return_items),
            "entry_return": (self._entry_return_events, self.entry_return_items),
            "writeoff": (self._writeoff_events, self.writeoff_items),
            "inventory": (self._inventory_events, self.inventory_adjustments),
        }
        if event_type and event_type in sources:
            return {event_type: sources[event_type]}
        return sources

    def _entries_events(self, take: int):
        qs = (
            self.entry_items()
            .select_related("entry", "entry__store", "entry__supplier", "entry__created_by")
            .order_by("-entry__created_at", "-id")[:take]
        )
        return [
            {
                "type": "entry",
                "date": item.entry.created_at,
                "doc_id": item.entry_id,
                "store_id": item.entry.store_id,
                "store_name": item.entry.store.name,
                "quantity": item.quantity,
                "price": item.purchase_price,
                "amount": item.quantity * item.purchase_price,
                "counterparty": item.entry.supplier.name if item.entry.supplier_id else None,
                "user": getattr(item.entry.created_by, "full_name", None),
                "status": None,
                "note": item.entry.note or "",
            }
            for item in qs
        ]

    def _entry_return_events(self, take: int):
        qs = (
            self.entry_return_items()
            .select_related(
                "stock_return",
                "stock_return__entry",
                "stock_return__entry__store",
                "stock_return__entry__supplier",
                "stock_return__created_by",
            )
            .order_by("-stock_return__created_at", "-id")[:take]
        )
        events = []
        for item in qs:
            entry = item.stock_return.entry
            events.append(
                {
                    "type": "entry_return",
                    "date": item.stock_return.created_at,
                    "doc_id": item.stock_return_id,
                    "store_id": entry.store_id,
                    "store_name": entry.store.name,
                    "quantity": item.quantity,
                    "price": item.purchase_price,
                    "amount": item.amount,
                    "counterparty": entry.supplier.name if entry.supplier_id else None,
                    "user": getattr(item.stock_return.created_by, "full_name", None),
                    "status": None,
                    "note": item.stock_return.note or "",
                }
            )
        return events

    def _transfer_events(self, take: int):
        qs = (
            self.transfer_items()
            .select_related(
                "stock_transfer",
                "stock_transfer__from_store",
                "stock_transfer__to_store",
                "stock_transfer__created_by",
            )
            .order_by("-stock_transfer__created_at", "-id")[:take]
        )
        return [
            {
                "type": "transfer",
                "date": item.stock_transfer.created_at,
                "doc_id": item.stock_transfer_id,
                "store_id": item.stock_transfer.from_store_id,
                "store_name": item.stock_transfer.from_store.name,
                "to_store_id": item.stock_transfer.to_store_id,
                "to_store_name": item.stock_transfer.to_store.name,
                "quantity": item.quantity,
                "price": item.purchase_price,
                "amount": (item.quantity * item.purchase_price) if item.purchase_price else None,
                "counterparty": None,
                "user": getattr(item.stock_transfer.created_by, "full_name", None),
                "status": item.stock_transfer.status,
                "note": item.stock_transfer.note or "",
            }
            for item in qs
        ]

    def _sale_events(self, take: int):
        qs = (
            self.sale_items()
            .select_related("sale", "sale__store", "sale__customer", "sale__seller")
            .order_by("-sale__created_at", "-id")[:take]
        )
        return [
            {
                "type": "sale",
                "date": item.sale.created_at,
                "doc_id": item.sale_id,
                "store_id": item.sale.store_id,
                "store_name": item.sale.store.name,
                "quantity": item.quantity,
                "price": item.unit_price,
                "amount": item.total_price,
                "counterparty": item.sale.customer.full_name if item.sale.customer_id else None,
                "user": getattr(item.sale.seller, "full_name", None),
                "status": item.sale.status,
                "note": "",
            }
            for item in qs
        ]

    def _sale_return_events(self, take: int):
        qs = (
            self.sale_return_items()
            .select_related(
                "sale_return",
                "sale_return__store",
                "sale_return__customer",
                "sale_return__seller",
            )
            .order_by("-sale_return__created_at", "-id")[:take]
        )
        return [
            {
                "type": "sale_return",
                "date": item.sale_return.created_at,
                "doc_id": item.sale_return_id,
                "store_id": item.sale_return.store_id,
                "store_name": item.sale_return.store.name,
                "quantity": item.quantity,
                "price": item.unit_price,
                "amount": item.total_price,
                "counterparty": item.sale_return.customer.full_name if item.sale_return.customer_id else None,
                "user": getattr(item.sale_return.seller, "full_name", None),
                "status": None,
                "note": item.sale_return.comment or "",
            }
            for item in qs
        ]

    def _writeoff_events(self, take: int):
        qs = (
            self.writeoff_items()
            .select_related("write_off", "write_off__store", "write_off__created_by")
            .order_by("-write_off__created_at", "-id")[:take]
        )
        return [
            {
                "type": "writeoff",
                "date": item.write_off.created_at,
                "doc_id": item.write_off_id,
                "store_id": item.write_off.store_id,
                "store_name": item.write_off.store.name,
                "quantity": item.quantity,
                "price": item.purchase_price,
                "amount": item.quantity * item.purchase_price,
                "counterparty": None,
                "user": getattr(item.write_off.created_by, "full_name", None),
                "status": item.write_off.reason,
                "note": item.write_off.note or "",
            }
            for item in qs
        ]

    def _inventory_events(self, take: int):
        qs = (
            self.inventory_adjustments()
            .select_related("session", "session__store", "session__started_by")
            .order_by("-created_at", "-id")[:take]
        )
        return [
            {
                "type": "inventory",
                "date": adjustment.created_at,
                "doc_id": adjustment.session_id,
                "store_id": adjustment.session.store_id,
                "store_name": adjustment.session.store.name,
                "quantity": adjustment.difference,
                "price": None,
                "amount": None,
                "counterparty": None,
                "user": getattr(adjustment.session.started_by, "full_name", None),
                "status": adjustment.session.status,
                "note": f"Inventarizatsiya #{adjustment.session_id} tafovuti: {adjustment.difference}",
            }
            for adjustment in qs
        ]

    def _merged_events(self, *, take: int, event_type: str | None = None):
        events = []
        for _, (build, _queryset) in self._event_sources(event_type).items():
            events.extend(build(take))
        events.sort(key=lambda e: e["date"], reverse=True)
        return events

    def collect_events(self, *, limit: int = 2000, event_type: str | None = None):
        return self._merged_events(take=limit, event_type=event_type)[:limit]

    def count_events(self, event_type: str | None = None) -> int:
        return sum(
            queryset().count()
            for _, (_build, queryset) in self._event_sources(event_type).items()
        )
