import openpyxl
from django.db import transaction
from django.core.exceptions import ValidationError

from apps.products.models import Product, Category, Brand, ProductUnitMeasurement


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
    # Inglizcha
    "name":             "name",
    "category":         "category",
    "brand":            "brand",
    "unit_measurement": "unit_measurement",
    "description":      "description",
    "min_stock":        "min_stock",
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

        # Qatorlarni parse qilish
        to_create = []
        errors    = []

        for row_num, row in enumerate(data_rows, start=2):
            name = cls._cell(row, col["name"])
            if not name:
                errors.append({"row": row_num, "error": "name bo'sh — qator o'tkazib yuborildi"})
                continue

            status_raw = cls._cell(row, col["status"]).lower()
            status     = VALID_STATUSES.get(status_raw, Product.ProductStatus.ACTIVE)
            unit_name  = cls._cell(row, col["unit_measurement"]) or "dona"
            min_stock  = cls._parse_int(cls._cell(row, col["min_stock"]), default=0)

            to_create.append((row_num, Product(
                name=name,
                category=category_map.get(cls._cell(row, col["category"]).lower()),
                brand=brand_map.get(cls._cell(row, col["brand"]).lower()),
                unit_measurement=unit_map.get(unit_name.lower()),
                description=cls._cell(row, col["description"]),
                status=status,
                min_stock=min_stock,
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