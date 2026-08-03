"""
Bitta mahsulotning HARAKAT TARIXI va statistikasi.

Manbalar (har biri alohida jadval — bittasi ham "harakatlar jurnali" emas):
  kirim            → contract.StockEntryItem
  kirim qaytimi    → contract.StockEntryReturnItem   (ta'minotchiga qaytarilgan)
  o'tkazma         → transfer.StockTransferItem      (do'konlar orasida)
  sotuv            → sales.SaleItem
  sotuv qaytimi    → sales.SaleReturnItem            (mijozdan qaytgan)
  spisaniye        → writeoff.WriteOffItem
  inventarizatsiya → inventory.InventoryAdjustment   (sanoqdagi farq)

DIQQAT — do'kon ruxsati (store scope): superuser hammasini ko'radi, xodim
faqat o'ziga biriktirilgan do'konlarning yozuvlarini. O'tkazmada ikki tomon
bor, shuning uchun "chiquvchi YOKI kiruvchi tomoni mening do'konim" qoidasi
qo'llanadi (aks holda xodim boshqa do'konlarning ichki harakatini ko'rardi).

Sotuvlarda `Sale.objects` (default manager) arxivlangan (deleted_at) sotuvlarni
chiqarib tashlaydi, lekin SaleItem'dan filtrlaganda bu manager ISHLAMAYDI —
shuning uchun `sale__deleted_at__isnull=True` qo'lda yoziladi.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
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

# Bitta so'rovda qaytariladigan hodisalar soni (sahifa hajmi) va eng chuqur
# sahifa. Hodisalar 7 xil jadvaldan yig'ilib, Python'da sana bo'yicha
# birlashtiriladi — chegara bo'lmasa katalogdagi "eng ko'p sotilgan" mahsulot
# uchun o'n minglab qator xotiraga ko'tarilardi.
MAX_EVENTS_WINDOW = 1000

# Miqdor × narx — DB tomonda hisoblanadi (Python'ga faqat yig'indi keladi)
MONEY = DecimalField(max_digits=20, decimal_places=2)


def _amount(qty_field: str, price_field: str):
    return Coalesce(Sum(F(qty_field) * F(price_field), output_field=MONEY), ZERO, output_field=MONEY)


def _qty(field: str = "quantity"):
    return Coalesce(Sum(field), 0)


def parse_date_param(value: str | None, *, end_of_day: bool = False):
    """'YYYY-MM-DD' → aware datetime; noto'g'ri/bo'sh qiymat → None (filtrsiz)."""
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    moment = datetime.combine(parsed.date(), time.max if end_of_day else time.min)
    return timezone.make_aware(moment, timezone.get_current_timezone())


class ProductHistoryService:
    """
    Mahsulot bo'yicha statistika + harakatlar tarixi.

    Foydalanish:
        service = ProductHistoryService(product, user, date_from=..., date_to=..., store_id=...)
        data = service.build(page=1, limit=50, event_type="sale")
    """

    def __init__(self, product, user, *, date_from=None, date_to=None, store_id=None):
        self.product = product
        self.user = user
        self.date_from = date_from
        self.date_to = date_to
        self.store_id = int(store_id) if str(store_id or "").isdigit() else None

        allowed = allowed_store_ids(user)
        # None — cheklanmagan (superuser). Aks holda faqat biriktirilgan do'konlar.
        self.allowed_store_ids = allowed
        if allowed is not None and self.store_id is not None and self.store_id not in allowed:
            # Ruxsatsiz do'kon so'ralgan — bo'sh natija (403 emas: sahifa filtri)
            self.allowed_store_ids = set()

        # only(): modeltranslation tufayli `name` ning barcha til variantlari ham
        # kerak — aks holda har bir do'kon uchun alohida deferred SQL chiqadi
        stores = Store.objects.only("id", "name", "name_uz", "name_uz_cyrl").order_by("name")
        if self.allowed_store_ids is not None:
            stores = stores.filter(id__in=self.allowed_store_ids)
        self.stores = list(stores)
        self.store_names = {s.id: s.name for s in self.stores}

    # ─────────────────────────────────────────────
    # Queryset'lar (filtr + scope bir joyda)
    # ─────────────────────────────────────────────

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
        # O'tkazmada ikki tomon: ruxsat yoki filtr ikkalasidan biriga mos kelsa yetarli
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

    # ─────────────────────────────────────────────
    # Joriy qoldiq (filtrga bog'liq emas — hozirgi holat)
    # ─────────────────────────────────────────────

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

    # ─────────────────────────────────────────────
    # Do'kon kesimi + umumiy xulosa
    # ─────────────────────────────────────────────

    def build_by_store(self):
        """
        Har bir do'kon uchun: kirim, sotuv, o'tkazma (kirdi/chiqdi), spisaniye,
        qaytimlar va joriy qoldiq. Har bir manba — 1 ta guruhlangan so'rov.
        """
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

        # Do'kon filtri yo'q bo'lsa — ro'yxat to'liq (harakatsiz do'kon ham 0 bilan).
        # Filtr bor bo'lsa faqat tanlangan do'kon oldindan qo'yiladi; qolganlari
        # o'tkazmaning ikkinchi tomoni sifatida o'zi qo'shiladi ("qayerga ketdi").
        if self.store_id is None:
            for store in self.stores:
                row(store.id)
        else:
            row(self.store_id)

        # DIQQAT: har bir guruhlashdan oldin .order_by() — modelning
        # Meta.ordering maydoni GROUP BY ga qo'shilib ketmasligi uchun
        # (StockTransferItem'da ordering bor; guruhlash jimgina buzilardi).
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

        # O'tkazma: faqat TASDIQLANGAN (qoldiqqa ta'sir qilgan) yo'nalishlar
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
        """Xulosa — do'kon kesimidagi qatorlardan yig'iladi (qo'shimcha SQL yo'q)."""
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

        # Sof sotuv (qaytimlar chegirilgan) va tannarx bo'yicha foyda
        # Tannarx faqat purchase_price yozilgan qatorlardan hisoblanadi —
        # eski/migratsiya qilingan sotuvlarda u NULL bo'lishi mumkin. Shunda
        # "foyda = tushum" bo'lib ko'rinmasligi uchun qamrov alohida qaytariladi
        # (costed_qty < sold_qty → frontend foydani "taxminiy" deb belgilaydi).
        sale_agg = self.sale_items().aggregate(
            cost=Coalesce(
                Sum(F("quantity") * F("purchase_price"), output_field=MONEY), ZERO, output_field=MONEY
            ),
            costed_qty=Coalesce(
                Sum("quantity", filter=Q(purchase_price__isnull=False)), 0
            ),
            sale_count=Count("id"),
        )
        returned_amount = self.sale_return_items().aggregate(
            amount=Coalesce(Sum("total_price"), ZERO, output_field=MONEY)
        )["amount"]

        totals["sale_returned_amount"] = returned_amount
        totals["net_sold_amount"] = totals["sold_amount"] - returned_amount
        totals["net_sold_qty"] = totals["sold_qty"] - totals["sale_returned_qty"]
        # Foyda = sotuv summasi − tannarx (ikkalasi ham sotuv qatorlaridan)
        totals["cost_amount"] = sale_agg["cost"] or ZERO
        totals["profit"] = totals["sold_amount"] - totals["cost_amount"]
        totals["costed_qty"] = sale_agg["costed_qty"] or 0
        # True — sotuvlarning bir qismida tannarx yo'q, foyda to'liq emas
        totals["profit_partial"] = totals["costed_qty"] < totals["sold_qty"]
        totals["sale_line_count"] = sale_agg["sale_count"] or 0

        transfers = self.transfer_items()
        totals["transferred_qty"] = transfers.aggregate(qty=_qty())["qty"]
        # .order_by() — DISTINCT bilan Meta.ordering maydoni SELECT ga tushmay
        # "ORDER BY expressions must appear in select list" xatosini bermasligi uchun
        totals["transfer_count"] = transfers.order_by().values("stock_transfer_id").distinct().count()

        entries = self.entry_items()
        totals["entry_count"] = entries.order_by().values("entry_id").distinct().count()
        totals["supplier_count"] = (
            entries.order_by().values("entry__supplier_id").distinct().count()
        )

        # O'rtacha narxlar (0 ga bo'linishdan himoya)
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

    # ─────────────────────────────────────────────
    # Oylik dinamika (grafik uchun)
    # ─────────────────────────────────────────────

    def build_monthly(self, months_limit: int = 24):
        buckets: dict[str, dict] = {}

        def bucket(dt):
            key = dt.strftime("%Y-%m")
            if key not in buckets:
                buckets[key] = {"month": key, "purchased_qty": 0, "sold_qty": 0, "sold_amount": ZERO}
            return buckets[key]

        for item in (
            self.entry_items()
            .order_by()
            .annotate(period=TruncMonth("entry__created_at"))
            .values("period")
            .annotate(qty=_qty())
        ):
            if item["period"]:
                bucket(item["period"])["purchased_qty"] = item["qty"]

        for item in (
            self.sale_items()
            .order_by()
            .annotate(period=TruncMonth("sale__created_at"))
            .values("period")
            .annotate(qty=_qty(), amount=Coalesce(Sum("total_price"), ZERO, output_field=MONEY))
        ):
            if item["period"]:
                row = bucket(item["period"])
                row["sold_qty"] = item["qty"]
                row["sold_amount"] = item["amount"]

        ordered = sorted(buckets.values(), key=lambda r: r["month"])
        return ordered[-months_limit:]

    # ─────────────────────────────────────────────
    # Ta'minotchilar kesimi
    # ─────────────────────────────────────────────

    def build_by_supplier(self, limit: int = 10):
        rows = (
            self.entry_items()
            .order_by()
            .values("entry__supplier_id", "entry__supplier__name")
            .annotate(qty=_qty(), amount=_amount("quantity", "purchase_price"))
            .order_by("-qty")[:limit]
        )
        return [
            {
                "supplier_id": row["entry__supplier_id"],
                "supplier_name": row["entry__supplier__name"] or "—",
                "qty": row["qty"],
                "amount": row["amount"],
            }
            for row in rows
        ]

    # ─────────────────────────────────────────────
    # Harakatlar tarixi (birlashtirilgan lenta)
    # ─────────────────────────────────────────────

    def _event_sources(self, event_type: str | None):
        """type → (count() funksiyasi, oxirgi N ta hodisani beruvchi funksiya)."""
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
        events = []
        for item in qs:
            transfer = item.stock_transfer
            events.append(
                {
                    "type": "transfer",
                    "date": transfer.created_at,
                    "doc_id": transfer.id,
                    "store_id": transfer.from_store_id,
                    "store_name": transfer.from_store.name if transfer.from_store_id else None,
                    "to_store_id": transfer.to_store_id,
                    "to_store_name": transfer.to_store.name if transfer.to_store_id else None,
                    "quantity": item.quantity,
                    "price": item.purchase_price,
                    "amount": item.quantity * item.purchase_price,
                    "counterparty": None,
                    "user": getattr(transfer.created_by, "full_name", None),
                    "status": transfer.status,
                    "note": "",
                }
            )
        return events

    def _sale_events(self, take: int):
        qs = (
            self.sale_items()
            .select_related("sale", "sale__store", "sale__seller", "sale__customer")
            .order_by("-sale__created_at", "-id")[:take]
        )
        events = []
        for item in qs:
            sale = item.sale
            events.append(
                {
                    "type": "sale",
                    "date": sale.created_at,
                    "doc_id": sale.id,
                    "store_id": sale.store_id,
                    "store_name": sale.store.name,
                    "quantity": item.quantity,
                    "price": item.unit_price,
                    "amount": item.total_price,
                    "counterparty": getattr(sale.customer, "full_name", None),
                    "user": getattr(sale.seller, "full_name", None),
                    "status": sale.status,
                    "note": "",
                }
            )
        return events

    def _sale_return_events(self, take: int):
        qs = (
            self.sale_return_items()
            .select_related(
                "sale_return", "sale_return__store", "sale_return__seller", "sale_return__customer"
            )
            .order_by("-sale_return__created_at", "-id")[:take]
        )
        events = []
        for item in qs:
            sale_return = item.sale_return
            events.append(
                {
                    "type": "sale_return",
                    "date": sale_return.created_at,
                    "doc_id": sale_return.id,
                    "store_id": sale_return.store_id,
                    "store_name": sale_return.store.name,
                    "quantity": item.quantity,
                    "price": item.unit_price,
                    "amount": item.total_price,
                    "counterparty": getattr(sale_return.customer, "full_name", None),
                    "user": getattr(sale_return.seller, "full_name", None),
                    "status": None,
                    "note": sale_return.comment or "",
                }
            )
        return events

    def _writeoff_events(self, take: int):
        qs = (
            self.writeoff_items()
            .select_related("write_off", "write_off__store", "write_off__created_by")
            .order_by("-write_off__created_at", "-id")[:take]
        )
        reasons = dict(WriteOff.Reason.choices)
        events = []
        for item in qs:
            write_off = item.write_off
            events.append(
                {
                    "type": "writeoff",
                    "date": write_off.created_at,
                    "doc_id": write_off.id,
                    "store_id": write_off.store_id,
                    "store_name": write_off.store.name,
                    "quantity": item.quantity,
                    "price": item.purchase_price,
                    "amount": item.quantity * item.purchase_price,
                    "counterparty": None,
                    "user": getattr(write_off.created_by, "full_name", None),
                    "status": write_off.reason,
                    "note": reasons.get(write_off.reason, "") or write_off.comment or "",
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
                    "store_name": session.store.name,
                    # difference: + ortiqcha chiqqan, − kamomad
                    "quantity": adjustment.difference,
                    "price": None,
                    "amount": None,
                    "counterparty": None,
                    "user": getattr(session.started_by, "full_name", None),
                    "status": session.status,
                    "note": "",
                }
            )
        return events

    def _merged_events(self, *, take: int, event_type: str | None = None):
        """Har manbadan `take` ta oxirgi yozuv olib, sana bo'yicha birlashtiradi."""
        events = []
        for _, (build, _queryset) in self._event_sources(event_type).items():
            events.extend(build(take))
        # Barcha sana maydonlari auto_now_add — NULL bo'lmaydi
        events.sort(key=lambda e: e["date"], reverse=True)
        return events

    def collect_events(self, *, limit: int = MAX_EVENTS_WINDOW, event_type: str | None = None):
        """
        Birlashtirilgan lentaning eng yangi `limit` tasi (sahifalashsiz).

        Har manbadan `limit` ta olinadi: birlashmaning yuqori `limit` tasi shu
        to'plam ichida albatta bor (undan oldingilari baribir pastda qoladi).
        Hisobot (mahsulot tarixi) shu ro'yxatni to'lig'icha oladi.
        """
        return self._merged_events(take=limit, event_type=event_type)[:limit]

    def count_events(self, event_type: str | None = None) -> int:
        """Filtrga mos hodisalarning umumiy soni (oyna chegarasidan qat'i nazar)."""
        return sum(
            queryset().count()
            for _, (_build, queryset) in self._event_sources(event_type).items()
        )

    def build_events(self, *, page: int = 1, limit: int = 50, event_type: str | None = None):
        """Birlashtirilgan lentani sahifalaydi (tarix sahifasi uchun)."""
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
            # Chuqur sahifalarda oyna chegarasi — frontend "hammasi emas" deb ogohlantiradi
            "truncated": total > MAX_EVENTS_WINDOW,
            "results": window,
        }

    def build(self, *, page: int = 1, limit: int = 50, event_type: str | None = None):
        by_store = self.build_by_store()
        return {
            "summary": self.build_summary(by_store),
            "by_store": by_store,
            "by_supplier": self.build_by_supplier(),
            "monthly": self.build_monthly(),
            "events": self.build_events(page=page, limit=limit, event_type=event_type),
            "stores": [{"id": s.id, "name": s.name} for s in self.stores],
        }
