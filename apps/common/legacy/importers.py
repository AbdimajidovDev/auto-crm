"""
Eski CRM Excel hisobotlarini bizning modellarga ko'chiruvchi IMPORTER'lar.

Har bir metod bitta bosqich (`constants.STEPS`):
  dicts      -> Store / Supplier / Category / Brand / Unit (lug'atlar)
  products   -> Product (katalog, "остатки"dan; eski sku/barcode saqlanadi)
  batches    -> ProductBatch (joriy qoldiq, "остатки"dan)
  entries    -> StockEntry + StockEntryItem ("импорты", ID заказа bo'yicha guruh)
  transfers  -> StockTransfer + StockTransferItem ("трансферы", ID bo'yicha guruh)
  sales      -> Sale + SaleItem ("продажи", (do'kon, sana) bo'yicha guruh)
  writeoffs  -> WriteOff + WriteOffItem ("списания", ID списания bo'yicha guruh)

MUHIM tamoyillar:
  * "остатки" — JORIY QOLDIQ uchun yagona haqiqat manbai (source of truth).
    Shuning uchun kirim/sotuv/transfer/spisaniye ko'chirilganda ProductBatch
    qoldig'i QAYTA o'zgartirilmaydi — faqat TARIXIY hujjatlar yoziladi.
  * Tarixiy sanalar: `created_at` (auto_now_add) bulk_create'da bugungi sanani
    qo'yadi; shuning uchun yozgandan SO'NG `bulk_update(["created_at"])` bilan
    Excel'dagi sanaga to'g'rilanadi (`_apply_created_at`).
  * Mahsulot sku/barcode: ProductResolver orqali topiladi; "остатки"da yo'q
    mahsulot uchragan joyda minimal Product yaratiladi (FK butunligi uchun).
"""
from decimal import Decimal

from django.db import transaction

from apps.contract.models import StockEntry, StockEntryItem
from apps.products.models import Product, ProductBatch
from apps.products.utils.barcode_utility import normalize_barcode
from apps.sales.models import Sale, SaleItem
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.writeoff.models import WriteOff, WriteOffItem

from . import constants as C
from .excel import LegacySheet, find_file
from .resolvers import DictionaryResolver, ProductResolver, get_system_user

TWO_PLACES = Decimal("0.01")
BATCH_SIZE = 1000


def _unit_price(total: Decimal, qty: int) -> Decimal:
    """Qatordagi jami summadan birlik narxini hisoblaydi (qty=0 bo'lsa 0)."""
    if qty <= 0:
        return Decimal("0.00")
    return (total / Decimal(qty)).quantize(TWO_PLACES)


class LegacyImporter:
    def __init__(self, docs_dir, *, stdout, dry_run=False):
        self.docs_dir = docs_dir
        self.stdout = stdout
        self.dry_run = dry_run
        self.dicts = DictionaryResolver(dry_run=dry_run)
        self.products = ProductResolver(dicts=self.dicts, dry_run=dry_run)

    # ── log ───────────────────────────────────────────────────────────────────
    def _log(self, msg):
        self.stdout.write(msg)

    def _sheet(self, key, columns):
        path = find_file(self.docs_dir, C.FILE_PATTERNS[key])
        self._log(f"  fayl: {path.name}")
        return LegacySheet.open(path, columns)

    def _apply_created_at(self, model, pairs):
        """
        pairs: [(obj, datetime), ...]. created_at (auto_now_add) bulk_create'dan
        keyin Excel sanasiga to'g'rilanadi. None sanalar o'tkazib yuboriladi.
        """
        to_update = []
        for obj, dt in pairs:
            if dt is not None:
                obj.created_at = dt
                to_update.append(obj)
        if to_update and not self.dry_run:
            for i in range(0, len(to_update), BATCH_SIZE):
                model.objects.bulk_update(to_update[i:i + BATCH_SIZE], ["created_at"])

    # ════════════════════════════════════════════════════════════════════════
    # 1) LUG'ATLAR
    # ════════════════════════════════════════════════════════════════════════
    @transaction.atomic
    def import_dicts(self):
        self._log("[dicts] Store / Supplier / Category / Brand / Unit ...")
        sheet = self._sheet("ostatki", C.OSTATKI_COLUMNS)
        for row in sheet.rows():
            self.dicts.store(row.text("store"))
            self.dicts.supplier(row.text("supplier"))
            self.dicts.category(row.text("category"))
            self.dicts.brand(row.text("brand"))
            self.dicts.unit(row.text("unit"))

        # Boshqa fayllarda uchraydigan, lekin "остатки"da bo'lmagan lug'atlar ham kerak.
        for key, cols in (
            ("importy", C.IMPORTY_COLUMNS),
            ("transfery", C.TRANSFERY_COLUMNS),
            ("spisaniya", C.SPISANIYA_COLUMNS),
        ):
            sheet = self._sheet(key, cols)
            for row in sheet.rows():
                self.dicts.store(row.text("store"))
                if "from_store" in cols:
                    self.dicts.store(row.text("from_store"))
                    self.dicts.store(row.text("to_store"))
                self.dicts.supplier(row.text("supplier"))
                self.dicts.category(row.text("category"))
                self.dicts.brand(row.text("brand"))

        c = self.dicts.created
        self._log(
            f"  yaratildi: store={c['store']} supplier={c['supplier']} "
            f"category={c['category']} brand={c['brand']} unit={c['unit']}"
        )

    # ════════════════════════════════════════════════════════════════════════
    # 2) MAHSULOTLAR (katalog) — "остатки"dan, eski sku/barcode saqlanadi
    # ════════════════════════════════════════════════════════════════════════
    @transaction.atomic
    def import_products(self):
        self._log("[products] Product katalogi ('остатки'dan) ...")
        sheet = self._sheet("ostatki", C.OSTATKI_COLUMNS)

        to_create = []
        seen_keys = set()        # sku yoki barcode bo'yicha dublikat oldini olish
        seen_barcodes = set()
        skipped_no_id = 0
        dropped_barcode = 0

        for row in sheet.rows():
            sku = row.text("sku")
            raw_barcode = row.text("barcode")
            key = sku or raw_barcode
            if not key:
                skipped_no_id += 1
                continue
            if key in seen_keys:
                continue
            # bazada allaqachon bor (qayta ishga tushirishda) — o'tkazamiz
            if self.products.sku_exists(sku) or self.products.barcode_exists(raw_barcode):
                seen_keys.add(key)
                continue
            seen_keys.add(key)

            # barcode'ni EAN-13'ga keltiramiz; yaroqsiz/dublikat -> None (avtomatik EMAS, bo'sh)
            barcode = None
            if raw_barcode:
                try:
                    barcode = normalize_barcode(raw_barcode)
                except ValueError:
                    barcode = None
                if barcode and (barcode in seen_barcodes or self.products.barcode_exists(barcode)):
                    barcode = None
                    dropped_barcode += 1
                if barcode:
                    seen_barcodes.add(barcode)

            to_create.append(self.products._build_product(
                sku=sku,
                barcode=barcode,
                name=row.text("name"),
                category=row.text("category"),
                brand=row.text("brand"),
                unit=row.text("unit"),
                archived=row.text("archived"),
            ))

        if not self.dry_run:
            # bulk_create -> Product.save() chaqirilmaydi => eski sku/barcode AYNAN saqlanadi
            # (avtomatik generatsiya va shtrix-rasm yasash chetlab o'tiladi).
            for i in range(0, len(to_create), BATCH_SIZE):
                chunk = Product.objects.bulk_create(to_create[i:i + BATCH_SIZE])
                for p in chunk:
                    self.products.register(p)

        self._log(
            f"  yaratiladi: {len(to_create)} mahsulot | "
            f"id'siz o'tkazildi: {skipped_no_id} | barcode bekor qilindi: {dropped_barcode}"
        )

    # ════════════════════════════════════════════════════════════════════════
    # 3) JORIY QOLDIQ -> ProductBatch ("остатки"dan)
    # ════════════════════════════════════════════════════════════════════════
    @transaction.atomic
    def import_batches(self):
        self._log("[batches] ProductBatch (joriy qoldiq) ...")
        sheet = self._sheet("ostatki", C.OSTATKI_COLUMNS)

        # (store_id, product_id) -> batch ma'lumoti. Dublikat bo'lsa miqdor qo'shiladi.
        grouped = {}
        missing_store = 0
        missing_product = 0

        for row in sheet.rows():
            store = self.dicts.store(row.text("store"))
            if store is None:
                missing_store += 1
                continue
            product_id = self.products.resolve_id(
                sku=row.text("sku"), barcode=row.text("barcode"),
                name=row.text("name"), category=row.text("category"),
                brand=row.text("brand"), unit=row.text("unit"),
                archived=row.text("archived"),
            )
            if product_id is None:
                missing_product += 1
                continue

            gkey = (store.id, product_id)
            data = grouped.get(gkey)
            qty = row.qty("quantity")
            if data is None:
                grouped[gkey] = {
                    "store_id": store.id,
                    "product_id": product_id,
                    "quantity": qty,
                    "purchase_price": row.dec("purchase_price"),
                    "selling_price": row.dec("selling_price"),
                    "created_at": row.dt("last_import"),
                }
            else:
                data["quantity"] += qty

        objs = [
            ProductBatch(
                store_id=d["store_id"],
                product_id=d["product_id"],
                quantity=d["quantity"],
                purchase_price=d["purchase_price"],
                selling_price=d["selling_price"],
            )
            for d in grouped.values()
        ]
        dates = [d["created_at"] for d in grouped.values()]

        if not self.dry_run:
            for i in range(0, len(objs), BATCH_SIZE):
                ProductBatch.objects.bulk_create(objs[i:i + BATCH_SIZE])
            self._apply_created_at(ProductBatch, list(zip(objs, dates)))

        self._log(
            f"  yaratiladi: {len(objs)} batch | do'konsiz: {missing_store} | "
            f"mahsulotsiz: {missing_product}"
        )

    # ════════════════════════════════════════════════════════════════════════
    # 4) KIRIM -> StockEntry + StockEntryItem ("импорты", ID заказа bo'yicha guruh)
    # ════════════════════════════════════════════════════════════════════════
    @transaction.atomic
    def import_entries(self):
        self._log("[entries] StockEntry + StockEntryItem ('импорты') ...")
        sheet = self._sheet("importy", C.IMPORTY_COLUMNS)

        groups = {}   # order_id -> {meta, items[]}
        skipped = 0

        for row in sheet.rows():
            order_id = row.text("order_id")
            store = self.dicts.store(row.text("store"))
            if not order_id or store is None:
                skipped += 1
                continue
            product_id = self.products.resolve_id(
                sku=row.text("sku"), barcode=row.text("barcode"),
                name=row.text("name"), category=row.text("category"),
                brand=row.text("brand"),
            )
            if product_id is None:
                skipped += 1
                continue

            qty = row.qty("quantity")
            if qty <= 0:
                continue
            purchase_total = row.dec("purchase_total")
            selling_total = row.dec("selling_total")

            g = groups.get(order_id)
            if g is None:
                g = groups[order_id] = {
                    "store_id": store.id,
                    "supplier": self.dicts.supplier(row.text("supplier")),
                    "created_at": row.dt("date"),
                    "items": [],
                    "total": Decimal("0.00"),
                }
            g["items"].append({
                "product_id": product_id,
                "quantity": qty,
                "purchase_price": _unit_price(purchase_total, qty),
                "selling_price": _unit_price(selling_total, qty),
            })
            g["total"] += purchase_total

        self._create_documents(
            groups,
            parent_model=StockEntry,
            item_model=StockEntryItem,
            build_parent=lambda g: StockEntry(
                supplier=g["supplier"],
                store_id=g["store_id"],
                total_amount=g["total"],
                # save() chetlab o'tilgani uchun to'lov maydonlarini qo'lda to'ldiramiz:
                # to'lovsiz (qarzga) kirim deb yoziladi.
                cash_amount=Decimal("0.00"),
                card_amount=Decimal("0.00"),
                paid_amount=Decimal("0.00"),
                debt_amount=g["total"],
                payment_type=StockEntry.PaymentType.Cash,
                created_by=self.system_user,
            ),
            build_item=lambda parent, it: StockEntryItem(
                entry_id=parent.id,
                product_id=it["product_id"],
                quantity=it["quantity"],
                purchase_price=it["purchase_price"],
                selling_price=it["selling_price"],
            ),
            require_supplier=True,
        )

    # ════════════════════════════════════════════════════════════════════════
    # 5) TRANSFER -> StockTransfer + StockTransferItem ("трансферы", ID bo'yicha)
    # ════════════════════════════════════════════════════════════════════════
    @transaction.atomic
    def import_transfers(self):
        self._log("[transfers] StockTransfer + StockTransferItem ('трансферы') ...")
        sheet = self._sheet("transfery", C.TRANSFERY_COLUMNS)

        groups = {}
        skipped = 0

        for row in sheet.rows():
            transfer_id = row.text("transfer_id")
            from_store = self.dicts.store(row.text("from_store"))
            to_store = self.dicts.store(row.text("to_store"))
            if not transfer_id or from_store is None or to_store is None:
                skipped += 1
                continue
            product_id = self.products.resolve_id(
                sku=row.text("sku"), barcode=row.text("barcode"),
                name=row.text("name"), category=row.text("category"),
                brand=row.text("brand"), unit=row.text("unit"),
            )
            if product_id is None:
                skipped += 1
                continue

            qty = row.qty("received_qty") or row.qty("sent_qty")
            if qty <= 0:
                continue

            g = groups.get(transfer_id)
            if g is None:
                g = groups[transfer_id] = {
                    "from_store_id": from_store.id,
                    "to_store_id": to_store.id,
                    "created_at": row.dt("sent_at"),
                    "approved_at": row.dt("received_at"),
                    "items": [],
                }
            g["items"].append({
                "product_id": product_id,
                "quantity": qty,
                "purchase_price": _unit_price(row.dec("purchase_total"), qty),
                "selling_price": _unit_price(row.dec("selling_total"), qty),
                "sent_at": row.dt("sent_at"),
            })

        self._create_documents(
            groups,
            parent_model=StockTransfer,
            item_model=StockTransferItem,
            build_parent=lambda g: StockTransfer(
                from_store_id=g["from_store_id"],
                to_store_id=g["to_store_id"],
                status=StockTransfer.Status.APPROVED,
                created_by=self.system_user,
                approved_by=self.system_user,
                approved_at=g["approved_at"],
            ),
            build_item=lambda parent, it: StockTransferItem(
                stock_transfer_id=parent.id,
                product_id=it["product_id"],
                quantity=it["quantity"],
                purchase_price=it["purchase_price"],
                selling_price=it["selling_price"],
            ),
            # StockTransferItem.created_at ham auto_now_add — uni "Отправлено"ga moslaymiz
            item_created_at=lambda it: it["sent_at"],
        )

    # ════════════════════════════════════════════════════════════════════════
    # 6) SOTUV -> Sale + SaleItem ("продажи", (do'kon, sana) bo'yicha guruh)
    # ════════════════════════════════════════════════════════════════════════
    @transaction.atomic
    def import_sales(self):
        self._log("[sales] Sale + SaleItem ('продажи', kunlik yig'ma) ...")
        sheet = self._sheet("prodaji", C.PRODAJI_COLUMNS)

        groups = {}
        skipped = 0

        for row in sheet.rows():
            store = self.dicts.store(row.text("store"))
            day = row.dt("date")
            if store is None or day is None:
                skipped += 1
                continue
            sold = row.qty("sold_qty")
            if sold <= 0:
                continue
            product_id = self.products.resolve_id(
                sku=row.text("sku"), barcode=row.text("barcode"),
                name=row.text("name"), category=row.text("category"),
                unit=row.text("unit"),
            )
            if product_id is None:
                skipped += 1
                continue

            revenue = row.dec("revenue")
            gkey = (store.id, day.date())
            g = groups.get(gkey)
            if g is None:
                g = groups[gkey] = {
                    "store_id": store.id,
                    "created_at": day,
                    "items": [],
                    "total": Decimal("0.00"),
                }
            g["items"].append({
                "product_id": product_id,
                "quantity": sold,
                "unit_price": _unit_price(revenue, sold),
                "total_price": revenue,
                "returned_quantity": row.qty("returned_qty"),
            })
            g["total"] += revenue

        self._create_documents(
            groups,
            parent_model=Sale,
            item_model=SaleItem,
            build_parent=lambda g: Sale(
                store_id=g["store_id"],
                seller=self.system_user,
                customer=None,
                total_amount=g["total"],
                paid_amount=g["total"],
                status=Sale.Status.PAID,
            ),
            build_item=lambda parent, it: SaleItem(
                sale_id=parent.id,
                product_id=it["product_id"],
                quantity=it["quantity"],
                unit_price=it["unit_price"],
                total_price=it["total_price"],
                returned_quantity=it["returned_quantity"],
                purchase_price=None,
            ),
        )

    # ════════════════════════════════════════════════════════════════════════
    # 7) SPISANIYE -> WriteOff + WriteOffItem ("списания", ID списания bo'yicha)
    # ════════════════════════════════════════════════════════════════════════
    @transaction.atomic
    def import_writeoffs(self):
        self._log("[writeoffs] WriteOff + WriteOffItem ('списания') ...")
        sheet = self._sheet("spisaniya", C.SPISANIYA_COLUMNS)

        groups = {}
        skipped = 0

        for row in sheet.rows():
            writeoff_id = row.text("writeoff_id")
            store = self.dicts.store(row.text("store"))
            if not writeoff_id or store is None:
                skipped += 1
                continue
            product_id = self.products.resolve_id(
                sku=row.text("sku"), barcode=row.text("barcode"),
                name=row.text("name"), category=row.text("category"),
                brand=row.text("brand"),
            )
            if product_id is None:
                skipped += 1
                continue

            qty = row.qty("quantity")
            if qty <= 0:
                continue
            purchase_price = row.dec("purchase_price")

            g = groups.get(writeoff_id)
            if g is None:
                reason_key = WriteOff.Reason(
                    C.WRITEOFF_REASON_MAP.get(row.text("reason").strip().lower(),
                                              WriteOff.Reason.OTHER)
                )
                comment = row.text("description") or row.text("title")
                g = groups[writeoff_id] = {
                    "store_id": store.id,
                    "reason": reason_key,
                    "comment": comment,
                    "created_at": row.dt("created_at"),
                    "items": [],
                    "total": Decimal("0.00"),
                }
            g["items"].append({
                "product_id": product_id,
                "quantity": qty,
                "purchase_price": purchase_price,
                "selling_price": row.dec("selling_price"),
            })
            g["total"] += purchase_price * Decimal(qty)

        self._create_documents(
            groups,
            parent_model=WriteOff,
            item_model=WriteOffItem,
            build_parent=lambda g: WriteOff(
                store_id=g["store_id"],
                reason=g["reason"],
                comment=g["comment"],
                total_amount=g["total"],
                created_by=self.system_user,
            ),
            build_item=lambda parent, it: WriteOffItem(
                write_off_id=parent.id,
                product_id=it["product_id"],
                quantity=it["quantity"],
                purchase_price=it["purchase_price"],
                selling_price=it["selling_price"],
            ),
        )

    # ════════════════════════════════════════════════════════════════════════
    # Umumiy: "hujjat + qatorlar" yozish (parent -> items), created_at to'g'rilash
    # ════════════════════════════════════════════════════════════════════════
    def _create_documents(self, groups, *, parent_model, item_model,
                          build_parent, build_item,
                          item_created_at=None, require_supplier=False):
        if not groups:
            self._log("  guruh topilmadi — o'tkazib yuborildi.")
            return

        group_list = list(groups.values())

        if require_supplier:
            no_supplier = sum(1 for g in group_list if g.get("supplier") is None)
            if no_supplier:
                self._log(f"  ⚠ postavshiksiz {no_supplier} hujjat — o'tkazib yuborildi.")
            group_list = [g for g in group_list if g.get("supplier") is not None]

        total_items = sum(len(g["items"]) for g in group_list)

        if self.dry_run:
            self._log(f"  [dry-run] {len(group_list)} hujjat, {total_items} qator yoziladi.")
            return

        # 1) Parent'larni bulk_create (Postgres/SQLite pk qaytaradi)
        parents = [build_parent(g) for g in group_list]
        created_parents = []
        for i in range(0, len(parents), BATCH_SIZE):
            created_parents += parent_model.objects.bulk_create(parents[i:i + BATCH_SIZE])

        # 2) created_at (auto_now_add) -> Excel sanasiga to'g'rilash
        self._apply_created_at(
            parent_model,
            [(p, g["created_at"]) for p, g in zip(created_parents, group_list)],
        )

        # 3) Qatorlarni bulk_create
        items = []
        item_dates = []
        for parent, g in zip(created_parents, group_list):
            for it in g["items"]:
                items.append(build_item(parent, it))
                if item_created_at is not None:
                    item_dates.append(item_created_at(it))
        for i in range(0, len(items), BATCH_SIZE):
            item_model.objects.bulk_create(items[i:i + BATCH_SIZE])

        # 4) Qatorlarning created_at'i (faqat o'z auto_now_add'i bo'lgan modellarda)
        if item_created_at is not None:
            self._apply_created_at(item_model, list(zip(items, item_dates)))

        self._log(f"  yozildi: {len(created_parents)} hujjat, {len(items)} qator.")

    # ── system user (lazy) ──────────────────────────────────────────────────
    @property
    def system_user(self):
        if not hasattr(self, "_system_user"):
            self._system_user = None if self.dry_run else get_system_user()
        return self._system_user
