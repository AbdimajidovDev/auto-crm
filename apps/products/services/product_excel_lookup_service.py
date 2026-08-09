"""
Excel shablondan mahsulotlarni topib berish (sotuv chekini fayl bilan to'ldirish uchun).

Import QILMAYDI — hech narsa yozmaydi, faqat o'qiydi va mos mahsulotlarni qaytaradi.
Mahsulot shabloni (`mahsulot_shablon.xlsx`) ham, kirim shabloni (`kirim_shablon.xlsx`)
ham, ular asosidagi erkin fayllar ham tushunilaveradi — ustunlar sarlavha nomi
bo'yicha topiladi, tartibiga bog'liq emas.

Qidiruv ustuvorligi: SKU (artikul) → barcode → nom.
"""
import openpyxl
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.functions import Lower

from apps.products.models import Product, ProductBatch

# Bir faylda ko'pi bilan shuncha ma'lumot qatori o'qiladi — juda katta fayl
# so'rovni bloklab qo'ymasligi uchun (qolganlari kesilgani javobda aytiladi)
MAX_DATA_ROWS = 2000
# Sarlavha qatori faylning boshida bo'lmasligi mumkin (logotip, izoh qatorlari)
HEADER_SCAN_ROWS = 10

SKU_HEADERS = {"sku", "artikul", "артикул", "article", "kod", "код"}
BARCODE_HEADERS = {
    "barcode", "shtrix", "shtrix kod", "shtrix-kod", "shtrixkod",
    "штрих", "штрих-код", "штрихкод", "ean", "ean13", "ean-13",
}
NAME_HEADERS = {
    "nomi", "nom", "mahsulot", "mahsulot nomi", "tovar", "tovar nomi",
    "name", "product", "product name", "наименование", "товар", "название",
}
QTY_HEADERS = {
    "miqdori", "miqdor", "soni", "son", "dona", "count",
    "quantity", "qty", "количество", "кол-во",
}


def _norm_header(value) -> str:
    """Sarlavhani solishtirish uchun normallashtiradi: 'Miqdori *' → 'miqdori'."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    # Majburiylik belgisi va ortiqcha tinish belgilari hisobga olinmaydi
    return text.rstrip("*").strip().rstrip(":").strip()


def _cell_text(value) -> str:
    """
    Katak qiymatini matnga o'giradi.

    Excel raqamli SKU/barcode'ni float qilib beradi (4780012345678.0) —
    shunday holatda kasr qismi tashlanadi, aks holda hech qachon topilmasdi.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _parse_qty(value) -> float:
    """Miqdor: kasr qabul qilinadi va 0.5 qadamga yaxlitlanadi (yarim juft).
    int() bilan kesilmaydi — juft mahsulotda 2.5 juft yozilgan bo'lishi mumkin."""
    try:
        qty = float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return 0
    qty = round(qty * 2) / 2
    return max(qty, 0)


class ProductExcelLookupService:
    """Excel qatorlarini bazadagi mahsulotlarga bog'laydi."""

    @classmethod
    def resolve(cls, file, store_id) -> dict:
        rows, columns, truncated, first_data_row = cls._read_rows(file)

        if not columns:
            raise ValidationError(
                "Ustunlar topilmadi. Faylda kamida 'SKU', 'Barcode' yoki 'Nomi' "
                "ustunlaridan biri bo'lishi kerak."
            )
        if not rows:
            raise ValidationError("Faylda ma'lumot qatorlari yo'q.")

        parsed = cls._parse_rows(rows, columns, first_data_row)
        if not parsed:
            raise ValidationError("Faylda to'ldirilgan qator topilmadi.")

        # ── Bazadan qidirish: har identifikator turi uchun BITTA so'rov (N+1 yo'q) ──
        skus = {p["sku"].lower() for p in parsed if p["sku"]}
        barcodes = {p["barcode"] for p in parsed if p["barcode"]}
        names = {p["name"].lower() for p in parsed if p["name"]}

        by_sku = cls._map_by_sku(skus)
        by_barcode = cls._map_by_barcode(barcodes)
        by_name, ambiguous_names = cls._map_by_name(names)

        # ── Qatorlarni mahsulotga bog'lash ──
        # Bir mahsulot bir necha qatorda kelsa — miqdorlar qo'shilib, bitta
        # qator bo'ladi (chekda dublikat paydo bo'lmasligi uchun)
        matched: dict[int, dict] = {}
        order: list[int] = []
        missing: list[dict] = []

        for item in parsed:
            product = None
            matched_by = ""
            if item["sku"]:
                product = by_sku.get(item["sku"].lower())
                matched_by = "sku"
            if product is None and item["barcode"]:
                product = by_barcode.get(item["barcode"])
                matched_by = "barcode"
            if product is None and item["name"]:
                key = item["name"].lower()
                if key in ambiguous_names:
                    missing.append({**cls._missing_payload(item), "reason": "ambiguous"})
                    continue
                product = by_name.get(key)
                matched_by = "name"

            if product is None:
                missing.append({**cls._missing_payload(item), "reason": "not_found"})
                continue

            qty = item["quantity"] or 1
            existing = matched.get(product.id)
            if existing:
                existing["quantity"] += qty
                existing["rows"].append(item["row"])
                continue

            matched[product.id] = {
                "product_id": product.id,
                "name": product.name,
                "sku": product.sku or "",
                "barcode": product.barcode or "",
                "quantity": qty,
                "matched_by": matched_by,
                "rows": [item["row"]],
            }
            order.append(product.id)

        # ── Tanlangan do'kondagi qoldiq va narxlar: bitta so'rov ──
        batches = {
            b.product_id: b
            for b in ProductBatch.objects.filter(
                store_id=store_id, product_id__in=list(matched.keys())
            )
        }
        for product_id, row in matched.items():
            batch = batches.get(product_id)
            row["available"] = batch.quantity if batch else 0
            row["purchase_price"] = str(batch.purchase_price) if batch else "0"
            row["selling_price"] = str(batch.selling_price) if batch else "0"
            row["wholesale_price"] = str(batch.wholesale_price) if batch else "0"

        # Topilmagan qatorlar orasida nofaol (arxivlangan) mahsulotlar bo'lishi
        # mumkin — "topilmadi" o'rniga aniq sabab ko'rsatiladi
        cls._mark_inactive(missing)

        items = [matched[pid] for pid in order]
        return {
            "store": int(store_id),
            "items": items,
            "missing": missing,
            "summary": {
                "rows": len(parsed),
                "found": len(items),
                "missing": len(missing),
                "merged": sum(len(i["rows"]) - 1 for i in items),
                "truncated": truncated,
                "max_rows": MAX_DATA_ROWS,
            },
        }

    # ── Yordamchi metodlar ────────────────────────────────────────────────────

    @staticmethod
    def _missing_payload(item: dict) -> dict:
        return {
            "row": item["row"],
            "sku": item["sku"],
            "barcode": item["barcode"],
            "name": item["name"],
            "quantity": item["quantity"] or 1,
        }

    @staticmethod
    def _mark_inactive(missing: list[dict]) -> None:
        """
        Topilmagan qatorlarni nofaol mahsulotlar bilan solishtiradi.
        Bitta qo'shimcha so'rov, faqat topilmaganlar bo'lganda ishlaydi.
        """
        rows = [m for m in missing if m.get("reason") == "not_found"]
        if not rows:
            return
        skus = {m["sku"].lower() for m in rows if m["sku"]}
        barcodes = {m["barcode"] for m in rows if m["barcode"]}
        names = {m["name"].lower() for m in rows if m["name"]}
        if not (skus or barcodes or names):
            return

        qs = Product.objects.exclude(status=Product.ProductStatus.ACTIVE).annotate(
            _lsku=Lower("sku"), _lname=Lower("name")
        )
        condition = Q()
        if skus:
            condition |= Q(_lsku__in=skus)
        if barcodes:
            condition |= Q(barcode__in=barcodes)
        if names:
            condition |= Q(_lname__in=names)

        found_skus, found_barcodes, found_names = set(), set(), set()
        for product in qs.filter(condition):
            if product.sku:
                found_skus.add(product.sku.lower())
            if product.barcode:
                found_barcodes.add(product.barcode)
            found_names.add(product.name.lower())

        for item in rows:
            if (
                (item["sku"] and item["sku"].lower() in found_skus)
                or (item["barcode"] and item["barcode"] in found_barcodes)
                or (item["name"] and item["name"].lower() in found_names)
            ):
                item["reason"] = "inactive"

    @classmethod
    def _read_rows(cls, file):
        """Faylni o'qiydi, sarlavha qatorini topadi va ustun indekslarini qaytaradi."""
        try:
            wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
        except Exception:
            raise ValidationError(
                "Excel faylni o'qib bo'lmadi. Fayl .xlsx formatida bo'lishi kerak."
            )

        try:
            # Ko'p shablonda birinchi varaq ma'lumot, ikkinchisi "Qo'llanma" —
            # shuning uchun faol varaq olinadi
            ws = wb.active
            all_rows = []
            for row in ws.iter_rows(values_only=True):
                all_rows.append(row)
                # Sarlavha + ma'lumot + zaxira: keraksiz katta faylni to'liq o'qimaymiz
                if len(all_rows) > HEADER_SCAN_ROWS + MAX_DATA_ROWS + 1:
                    break
        finally:
            wb.close()

        header_index, columns = cls._find_header(all_rows)
        if columns is None:
            return [], None, False, 0

        data_rows = all_rows[header_index + 1:]
        truncated = len(data_rows) > MAX_DATA_ROWS
        # Excel qator raqamlari 1 dan boshlanadi: sarlavha indeksi 0 bo'lsa
        # birinchi ma'lumot qatori 2-qator
        first_data_row = header_index + 2
        return data_rows[:MAX_DATA_ROWS], columns, truncated, first_data_row

    @staticmethod
    def _find_header(all_rows):
        """
        Birinchi HEADER_SCAN_ROWS qator ichidan sarlavha qatorini topadi:
        SKU / barcode / nom ustunlaridan kamida bittasi bo'lgan qator.
        """
        for index, row in enumerate(all_rows[:HEADER_SCAN_ROWS]):
            if row is None:
                continue
            columns = {}
            for col_index, raw in enumerate(row):
                header = _norm_header(raw)
                if not header:
                    continue
                if header in SKU_HEADERS and "sku" not in columns:
                    columns["sku"] = col_index
                elif header in BARCODE_HEADERS and "barcode" not in columns:
                    columns["barcode"] = col_index
                elif header in NAME_HEADERS and "name" not in columns:
                    columns["name"] = col_index
                elif header in QTY_HEADERS and "quantity" not in columns:
                    columns["quantity"] = col_index
            # Miqdor yolg'iz o'zi yetarli emas — identifikator ustuni shart
            if columns.keys() & {"sku", "barcode", "name"}:
                return index, columns
        return -1, None

    @staticmethod
    def _parse_rows(data_rows, columns, first_data_row: int = 2) -> list[dict]:
        parsed = []
        # Qator raqami foydalanuvchi Excel'da ko'radigan raqam bilan mos bo'lishi
        # uchun sarlavha qayerda topilganidan hisoblanadi
        for offset, row in enumerate(data_rows):
            if row is None:
                continue
            width = len(row)

            def cell(key: str) -> str:
                idx = columns.get(key)
                if idx is None or idx >= width:
                    return ""
                return _cell_text(row[idx])

            sku = cell("sku")
            barcode = cell("barcode")
            name = cell("name")
            if not (sku or barcode or name):
                continue  # bo'sh qator

            qty_idx = columns.get("quantity")
            quantity = 0
            if qty_idx is not None and qty_idx < width:
                quantity = _parse_qty(row[qty_idx])

            parsed.append({
                "row": first_data_row + offset,
                "sku": sku,
                "barcode": barcode,
                "name": name,
                "quantity": quantity,
            })
        return parsed

    @staticmethod
    def _map_by_sku(skus: set) -> dict:
        if not skus:
            return {}
        qs = (
            Product.objects.filter(status=Product.ProductStatus.ACTIVE)
            .annotate(_lsku=Lower("sku"))
            .filter(_lsku__in=skus)
        )
        return {p.sku.lower(): p for p in qs if p.sku}

    @staticmethod
    def _map_by_barcode(barcodes: set) -> dict:
        if not barcodes:
            return {}
        qs = Product.objects.filter(
            status=Product.ProductStatus.ACTIVE, barcode__in=barcodes
        )
        return {p.barcode: p for p in qs if p.barcode}

    @staticmethod
    def _map_by_name(names: set):
        """
        Nom bo'yicha qidiruv. Nom unikal emas — bir nechta mahsulot mos kelsa
        qaysi biri kerakligi noma'lum, shuning uchun bunday nomlar "ambiguous"
        deb belgilanadi va chekka avtomatik qo'shilmaydi.
        """
        if not names:
            return {}, set()
        qs = (
            Product.objects.filter(status=Product.ProductStatus.ACTIVE)
            .annotate(_lname=Lower("name"))
            .filter(_lname__in=names)
        )
        by_name = {}
        ambiguous = set()
        for product in qs:
            key = product.name.lower()
            if key in by_name:
                ambiguous.add(key)
                continue
            by_name[key] = product
        for key in ambiguous:
            by_name.pop(key, None)
        return by_name, ambiguous
