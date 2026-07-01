"""
stock_entry_import_service.py — Excel orqali omborga (asosiy do'kon) KIRIM qilish.

Oqim:
    Excel fayl  →  parse + validatsiya  →  mahsulotlarni aniqlash (barcode/sku/nom)
              →  items ro'yxati  →  StockEntryService.create_entry()  →  StockEntry

Supplier, cash_amount, card_amount va do'kon API darajasida beriladi;
mahsulot satrlari (nom/barcode/sku, miqdor, narxlar) Excelda joylashadi.
"""
from decimal import Decimal, InvalidOperation

import openpyxl
from django.core.exceptions import ValidationError

from apps.products.models import Product
from apps.contract.services.stock_entry_service import StockEntryService


REQUIRED_COLUMNS = {"quantity", "purchase_price", "selling_price"}
IDENTIFIER_COLUMNS = {"barcode", "sku", "name"}

HEADER_MAP = {
    # Identifikatorlar
    "barcode":          "barcode",
    "shtrix kod":       "barcode",
    "shtrix-kod":       "barcode",
    "sku":              "sku",
    "artikul":          "sku",
    "name":             "name",
    "nomi":             "name",
    "mahsulot":         "name",
    "mahsulot nomi":    "name",
    # Miqdor
    "quantity":         "quantity",
    "miqdor":           "quantity",
    "miqdori":          "quantity",
    "soni":             "quantity",
    # Narxlar
    "purchase_price":     "purchase_price",
    "sotib olingan narx": "purchase_price",
    "kirim narxi":        "purchase_price",
    "tannarx":            "purchase_price",
    "selling_price":      "selling_price",
    "sotish narxi":       "selling_price",
    "sotuv narxi":        "selling_price",
    "wholesale_price":    "wholesale_price",
    "optom narx":         "wholesale_price",
    "ulgurji narx":       "wholesale_price",
}


class StockEntryImportService:

    @classmethod
    def import_from_excel(cls, *, file, supplier, store, cash_amount, card_amount, user) -> dict:
        """
        Excel fayldan kirim yaratadi.

        Qaytaradi:
            {
              "entry_id": int | None,   # yaratilgan kirim (valid satr bo'lmasa None)
              "created": int,           # kirimga qo'shilgan satrlar soni
              "skipped": [ {"row", "reason"} ],  # o'tkazib yuborilgan satrlar
              "total_amount": str,
              "paid_amount": str,
              "debt_amount": str,
              "payment_type": str | None,
            }
        """
        col, data_rows = cls._read_sheet(file)

        # ── 1. Parse + validatsiya ────────────────────────────────────────────
        parsed   = []   # {"row", "barcode", "sku", "name", "quantity", "purchase_price", "selling_price", "wholesale_price"}
        skipped  = []

        has = {key: (key in col) for key in IDENTIFIER_COLUMNS}

        for row_num, row in enumerate(data_rows, start=2):
            barcode = cls._cell(row, col["barcode"]) if has["barcode"] else ""
            sku     = cls._cell(row, col["sku"])     if has["sku"]     else ""
            name    = cls._cell(row, col["name"])    if has["name"]    else ""

            # To'liq bo'sh satrni jimgina o'tkazamiz
            if not any([barcode, sku, name]) and cls._row_is_empty(row, col):
                continue

            if not any([barcode, sku, name]):
                skipped.append({"row": row_num, "reason": "mahsulot identifikatori yo'q (barcode/sku/nom)"})
                continue

            quantity, err = cls._parse_int(cls._cell(row, col["quantity"]))
            if err:
                skipped.append({"row": row_num, "reason": f"miqdor: {err}"})
                continue

            purchase_price, err = cls._parse_decimal(cls._cell(row, col["purchase_price"]))
            if err:
                skipped.append({"row": row_num, "reason": f"sotib olingan narx: {err}"})
                continue

            selling_price, err = cls._parse_decimal(cls._cell(row, col["selling_price"]))
            if err:
                skipped.append({"row": row_num, "reason": f"sotish narxi: {err}"})
                continue

            if "wholesale_price" in col:
                wholesale_price, err = cls._parse_decimal(cls._cell(row, col["wholesale_price"]), default=Decimal("0"))
                if err:
                    skipped.append({"row": row_num, "reason": f"optom narx: {err}"})
                    continue
            else:
                wholesale_price = Decimal("0")

            reason = cls._validate_business(quantity, purchase_price, selling_price, wholesale_price)
            if reason:
                skipped.append({"row": row_num, "reason": reason})
                continue

            parsed.append({
                "row": row_num,
                "barcode": barcode,
                "sku": sku,
                "name": name,
                "quantity": quantity,
                "purchase_price": purchase_price,
                "selling_price": selling_price,
                "wholesale_price": wholesale_price,
            })

        # ── 2. Mahsulotlarni aniqlash (N+1 oldini olish uchun map'lar) ─────────
        product_maps = cls._build_product_maps(parsed)

        items     = []
        seen_pids = set()
        for p in parsed:
            product, reason = cls._resolve_product(p, product_maps)
            if product is None:
                skipped.append({"row": p["row"], "reason": reason})
                continue

            # Bitta kirimda bir mahsulot ikki marta kelsa — dublikat batch oldini olamiz
            if product.id in seen_pids:
                skipped.append({"row": p["row"], "reason": f"mahsulot satrlarda takrorlangan: {product.name}"})
                continue
            seen_pids.add(product.id)

            items.append({
                "product": product,
                "quantity": p["quantity"],
                "purchase_price": p["purchase_price"],
                "selling_price": p["selling_price"],
                "wholesale_price": p["wholesale_price"],
            })

        if not items:
            return {
                "entry_id": None,
                "created": 0,
                "skipped": skipped,
                "total_amount": "0.00",
                "paid_amount": "0.00",
                "debt_amount": "0.00",
                "payment_type": None,
            }

        # ── 3. To'lov validatsiyasi (faqat valid satrlar bo'yicha) ────────────
        total_amount = sum((i["purchase_price"] * i["quantity"] for i in items), Decimal("0"))
        paid_amount  = Decimal(cash_amount) + Decimal(card_amount)
        if paid_amount > total_amount:
            raise ValidationError(
                f"To'lov ({paid_amount}) umumiy kirim summasidan ({total_amount}) oshib ketdi."
            )

        # ── 4. Kirimni yaratish (mavjud servis qayta ishlatiladi) ─────────────
        entry = StockEntryService.create_entry(
            supplier=supplier,
            store=store,
            items=items,
            cash_amount=cash_amount,
            card_amount=card_amount,
            user=user,
        )

        return {
            "entry_id": entry.id,
            "created": len(items),
            "skipped": skipped,
            "total_amount": str(entry.total_amount),
            "paid_amount": str(entry.paid_amount),
            "debt_amount": str(entry.debt_amount),
            "payment_type": entry.payment_type,
        }

    # ── Excel o'qish ──────────────────────────────────────────────────────────

    @classmethod
    def _read_sheet(cls, file):
        try:
            wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
        except Exception:
            raise ValidationError("Excel faylni o'qib bo'lmadi. Fayl .xlsx formatida bo'lishi kerak.")

        ws = wb.active
        if ws is None:
            raise ValidationError("Excel faylda ishchi varaq topilmadi.")

        # YAXSHI: read_only=True + data_only=True - openpyxl formulalarni hisoblamaydi va lazy o'qiydi.
        # MUAMMO [KRITIK]: list(ws.iter_rows(...)) BARCHA satrlarni bir zumda xotiraga materializatsiya qiladi.
        #   ~48k qatorli faylda bu katta RAM va sekin request demak. iter_rows generatorining afzalligi yo'qoladi.
        # YECHIM: satr sonini oldindan cheklash yoki chunk-lab ishlash:
        #   MAX_ROWS = 5000; ws.max_row tekshiruvi yoki enumerate(ws.iter_rows(...)) ni generator sifatida uzatish.
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            raise ValidationError("Excel fayl bo'sh.")

        # Sarlavhalarni normallashtiramiz: majburiylik belgisi "*" va ortiqcha bo'shliqlar olib tashlanadi.
        raw_headers    = [str(h).strip().lower().rstrip("*").strip() if h is not None else "" for h in rows[0]]
        mapped_headers = [HEADER_MAP.get(h, h) for h in raw_headers]
        col = {name: idx for idx, name in enumerate(mapped_headers)}

        missing = REQUIRED_COLUMNS - set(mapped_headers)
        if missing:
            raise ValidationError(f"Ustunlar topilmadi: {', '.join(sorted(missing))}")

        if not (IDENTIFIER_COLUMNS & set(mapped_headers)):
            raise ValidationError(
                "Kamida bitta mahsulot identifikatori ustuni kerak: 'Barcode', 'SKU' yoki 'Mahsulot nomi'."
            )

        data_rows = rows[1:]
        if not data_rows:
            raise ValidationError("Shablon bo'sh — ma'lumot qatorlari yo'q.")

        return col, data_rows

    # ── Mahsulotlarni aniqlash ─────────────────────────────────────────────────

    @staticmethod
    def _build_product_maps(parsed) -> dict:
        """barcode/sku/nom bo'yicha bitta-bittadan query — faqat ACTIVE mahsulotlar."""
        # YAXSHI: Mahsulotlar loop ichida emas, __in orqali 3 ta jamlangan query bilan olinadi (barcode/sku/name).
        #   Bu N+1 ni oldini oladi - _resolve_product keyin faqat xotiradagi map'dan o'qiydi. Namunali yechim.
        # Eslatma [PERF]: name uchun annotate(Lower("name")) filtri indeksdan foydalanmaydi (funktsional indeks yo'q),
        #   agar Product jadvali katta bo'lsa nom bo'yicha qidiruv sekin bo'lishi mumkin. barcode/sku afzal.
        barcodes = {p["barcode"] for p in parsed if p["barcode"]}
        skus     = {p["sku"] for p in parsed if p["sku"]}
        names    = {p["name"].lower() for p in parsed if p["name"]}

        active = Product.objects.filter(status=Product.ProductStatus.ACTIVE)

        barcode_map = {}
        if barcodes:
            barcode_map = {
                p.barcode: p
                for p in active.filter(barcode__in=barcodes)
            }

        sku_map = {}
        if skus:
            sku_map = {
                p.sku: p
                for p in active.filter(sku__in=skus)
            }

        # Nom case-insensitive; nom UNIKAL emas — bir nechta mos kelsa ambiguity belgilanadi.
        name_map = {}
        if names:
            from django.db.models.functions import Lower
            qs = active.annotate(_lname=Lower("name")).filter(_lname__in=names)
            for p in qs:
                key = p.name.lower()
                if key in name_map:
                    name_map[key] = "AMBIGUOUS"
                elif name_map.get(key) != "AMBIGUOUS":
                    name_map[key] = p

        return {"barcode": barcode_map, "sku": sku_map, "name": name_map}

    @staticmethod
    def _resolve_product(p, maps):
        """
        Ustuvorlik: barcode → sku → nom.
        Qaytaradi (product, None) yoki (None, sabab).
        """
        if p["barcode"]:
            product = maps["barcode"].get(p["barcode"])
            if product:
                return product, None
            return None, f"barcode bo'yicha faol mahsulot topilmadi: {p['barcode']}"

        if p["sku"]:
            product = maps["sku"].get(p["sku"])
            if product:
                return product, None
            return None, f"sku bo'yicha faol mahsulot topilmadi: {p['sku']}"

        # name
        match = maps["name"].get(p["name"].lower())
        if match == "AMBIGUOUS":
            return None, f"nom bir nechta mahsulotga mos keldi (barcode/sku ishlating): {p['name']}"
        if match:
            return match, None
        return None, f"nom bo'yicha faol mahsulot topilmadi: {p['name']}"

    # ── Yordamchi metodlar ─────────────────────────────────────────────────────

    @staticmethod
    def _cell(row, idx: int) -> str:
        val = row[idx]
        return str(val).strip() if val is not None else ""

    @staticmethod
    def _row_is_empty(row, col) -> bool:
        return all(StockEntryImportService._cell(row, idx) == "" for idx in col.values())

    @staticmethod
    def _parse_int(value: str):
        if value == "":
            return None, "bo'sh"
        try:
            parsed = int(float(value))
        except (ValueError, TypeError):
            return None, "raqam emas"
        if parsed <= 0:
            return None, "0 dan katta bo'lishi kerak"
        return parsed, None

    @staticmethod
    def _parse_decimal(value: str, default=None):
        if value == "":
            if default is not None:
                return default, None
            return None, "bo'sh"
        try:
            parsed = Decimal(value.replace(" ", "").replace(",", "."))
        except (InvalidOperation, ValueError, TypeError):
            return None, "raqam emas"
        if parsed < 0:
            return None, "manfiy bo'lmasligi kerak"
        return parsed, None

    @staticmethod
    def _validate_business(quantity, purchase_price, selling_price, wholesale_price):
        """StockEntryItemSerializer dagi qoidalar bilan bir xil."""
        if purchase_price <= 0:
            return "sotib olingan narx 0 dan katta bo'lishi kerak"
        if selling_price <= 0:
            return "sotish narxi 0 dan katta bo'lishi kerak"
        if selling_price < purchase_price:
            return "sotish narxi sotib olingan narxdan past bo'lmasligi kerak"
        if wholesale_price > 0:
            if wholesale_price < purchase_price:
                return "optom narx tannarxdan past bo'lmasligi kerak"
            if wholesale_price > selling_price:
                return "optom narx sotish narxidan yuqori bo'lmasligi kerak"
        return None


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Bu servis GET emas, lekin Excel import xavfi shu yerda joylashgan.
# YAXSHI: _build_product_maps mahsulotlarni __in bilan jamlab oladi - loop ichida query yo'q (N+1 yo'q);
#   read_only + data_only bilan openpyxl ochiladi.
# KRITIK: _read_sheet da list(ws.iter_rows()) butun faylni (~48k qator) xotiraga yuklaydi; satr/hajm limiti yo'q;
#   import bitta atomic tranzaksiyada - katta faylda RAM/timeout/lock xavfi.
# PERF: create_entry bitta chaqiriladi; agar u itemlarni loop ichida save qilsa (stock_entry_service.py da tekshirish
#   kerak) bulk_create afzal.
# Kritik muammolar soni: 1
# Performance muammolari: 1 (nom bo'yicha Lower filtri indeksdan foydalanmaydi)
# Arxitektura muammolari: 1 (massiv import sinxron - background task kerak)
# Umumiy baho: 6 / 10
# Prioritet boyicha birinchi hal qilinishi kerak: [iter_rows ni limit/chunk bilan cheklash + background task]
# ═══════════════════════════════
