import openpyxl
from django.db import transaction
from django.core.exceptions import ValidationError

from apps.products.models import Product, Category, Brand, ProductUnitMeasurement
from apps.products.utils.barcode_utility import normalize_barcode


VALID_STATUSES = {
    "active":   Product.ProductStatus.ACTIVE,
    "inactive": Product.ProductStatus.INACTIVE,
    "draft":    Product.ProductStatus.DRAFT,
}

REQUIRED_COLUMNS = {"name", "category", "brand", "unit_measurement", "description", "status", "min_stock"}

HEADER_MAP = {
    # O'zbekcha
    "nomi *":           "name",
    "nomi":             "name",
    "kategoriya":       "category",
    "brend":            "brand",
    "o'lchov birligi":  "unit_measurement",
    "tavsif":           "description",
    "status":           "status",
    "min. qoldiq":      "min_stock",
    "minimal qoldiq":   "min_stock",
    "shtrix kod":       "barcode",
    "artikul":          "sku",
    # Inglizcha
    "name":             "name",
    "category":         "category",
    "brand":            "brand",
    "unit_measurement": "unit_measurement",
    "description":      "description",
    "min_stock":        "min_stock",
    "barcode":          "barcode",
    "sku":              "sku",
}


class ProductImportService:

    @classmethod
    def import_from_excel(cls, file) -> dict:
        try:
            wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
        except Exception:
            raise ValidationError("Excel faylni o'qib bo'lmadi. Fayl .xlsx formatida bo'lishi kerak.")

        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))

        if not rows:
            raise ValidationError("Excel fayl bo'sh.")

        # Header normalize
        raw_headers    = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
        mapped_headers = [HEADER_MAP.get(h, h) for h in raw_headers]

        missing = REQUIRED_COLUMNS - set(mapped_headers)
        if missing:
            raise ValidationError(f"Ustunlar topilmadi: {', '.join(missing)}")

        col       = {name: idx for idx, name in enumerate(mapped_headers)}
        data_rows = rows[1:]

        if not data_rows:
            raise ValidationError("Shablon bo'sh — ma'lumot qatorlari yo'q.")

        # Lookup map'larni oldindan tayyorlash (N+1 oldini olish)
        raw_categories = {cls._cell(r, col["category"])        for r in data_rows}
        raw_brands     = {cls._cell(r, col["brand"])            for r in data_rows}
        raw_units      = {cls._cell(r, col["unit_measurement"]) for r in data_rows}

        raw_categories.discard("")
        raw_brands.discard("")
        raw_units.discard("")

        category_map = cls._build_category_map(raw_categories)
        brand_map    = cls._build_brand_map(raw_brands)
        unit_map     = cls._build_unit_map(raw_units)

        # barcode/sku ixtiyoriy ustunlar — bazada mavjudlarini oldindan yig'amiz (N+1 oldini olish)
        has_barcode = "barcode" in col
        has_sku     = "sku" in col
        existing_barcodes = cls._build_existing_barcodes(data_rows, col) if has_barcode else set()
        existing_skus     = cls._build_existing_skus(data_rows, col) if has_sku else set()

        # Qatorlarni parse qilish
        to_create = []
        errors    = []
        seen_barcodes = set()
        seen_skus     = set()

        for row_num, row in enumerate(data_rows, start=2):
            name = cls._cell(row, col["name"])
            if not name:
                errors.append({"row": row_num, "error": "name bo'sh — qator o'tkazib yuborildi"})
                continue

            status_raw = cls._cell(row, col["status"]).lower()
            status     = VALID_STATUSES.get(status_raw, Product.ProductStatus.ACTIVE)
            unit_name  = cls._cell(row, col["unit_measurement"]) or "dona"
            min_stock  = cls._parse_int(cls._cell(row, col["min_stock"]), default=0)

            # BARCODE: kelsa normallashtiriladi + takror tekshiriladi, kelmasa None (avtomatik)
            barcode_val = None
            if has_barcode:
                raw_bc = cls._cell(row, col["barcode"])
                if raw_bc:
                    try:
                        barcode_val = normalize_barcode(raw_bc)
                    except ValueError:
                        errors.append({"row": row_num, "error": "barcode yaroqsiz (faqat 12-13 raqamli EAN-13)"})
                        continue
                    if barcode_val in existing_barcodes or barcode_val in seen_barcodes:
                        errors.append({"row": row_num, "error": f"barcode takrorlangan: {barcode_val}"})
                        continue
                    seen_barcodes.add(barcode_val)

            # SKU: kelsa takror tekshiriladi, kelmasa None (avtomatik)
            sku_val = None
            if has_sku:
                raw_sku = cls._cell(row, col["sku"])
                if raw_sku:
                    if raw_sku in existing_skus or raw_sku in seen_skus:
                        errors.append({"row": row_num, "error": f"sku takrorlangan: {raw_sku}"})
                        continue
                    seen_skus.add(raw_sku)
                    sku_val = raw_sku

            to_create.append((row_num, Product(
                name=name,
                category=category_map.get(cls._cell(row, col["category"]).lower()),
                brand=brand_map.get(cls._cell(row, col["brand"]).lower()),
                unit_measurement=unit_map.get(unit_name.lower()),
                description=cls._cell(row, col["description"]),
                status=status,
                min_stock=min_stock,
                barcode=barcode_val,
                sku=sku_val,
            )))

        if not to_create:
            return {"created": 0, "skipped": len(data_rows), "errors": errors}

        # Har birini alohida save() — barcode/sku generatsiya bo'lishi uchun
        created_count = 0
        with transaction.atomic():
            for row_num, product in to_create:
                try:
                    product.save()
                    created_count += 1
                except Exception as e:
                    errors.append({"row": row_num, "error": str(e)})

        skipped = len(data_rows) - created_count - len(
            [e for e in errors if "o'tkazib yuborildi" in e["error"]]
        )

        return {
            "created": created_count,
            "skipped": max(skipped, 0),
            "errors":  errors,
        }

    # ── Yordamchi metodlar ────────────────────────────────────────────────────

    @staticmethod
    def _cell(row, idx: int) -> str:
        val = row[idx]
        return str(val).strip() if val is not None else ""

    @staticmethod
    def _parse_int(value: str, default: int = 0) -> int:
        try:
            parsed = int(float(value))
            return max(parsed, 0)   # manfiy bo'lmasin
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _build_existing_barcodes(data_rows, col) -> set:
        provided = set()
        for r in data_rows:
            raw = ProductImportService._cell(r, col["barcode"])
            if raw:
                try:
                    provided.add(normalize_barcode(raw))
                except ValueError:
                    pass
        if not provided:
            return set()
        return set(
            Product.objects.filter(barcode__in=provided).values_list("barcode", flat=True)
        )

    @staticmethod
    def _build_existing_skus(data_rows, col) -> set:
        provided = {
            ProductImportService._cell(r, col["sku"])
            for r in data_rows
        }
        provided.discard("")
        if not provided:
            return set()
        return set(
            Product.objects.filter(sku__in=provided).values_list("sku", flat=True)
        )

    @staticmethod
    def _build_category_map(names: set) -> dict:
        if not names:
            return {}
        return {c.name.lower(): c for c in Category.objects.filter(name__in=names)}

    @staticmethod
    def _build_brand_map(names: set) -> dict:
        if not names:
            return {}
        return {b.name.lower(): b for b in Brand.objects.filter(name__in=names)}

    @staticmethod
    def _build_unit_map(names: set) -> dict:
        names.add("dona")
        existing = ProductUnitMeasurement.objects.filter(measurement__in=names)
        unit_map = {u.measurement.lower(): u for u in existing}

        new_units = [
            ProductUnitMeasurement(measurement=name)
            for name in names
            if name.lower() not in unit_map
        ]
        if new_units:
            for u in ProductUnitMeasurement.objects.bulk_create(new_units):
                unit_map[u.measurement.lower()] = u

        return unit_map