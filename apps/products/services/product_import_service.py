import openpyxl
from django.db import transaction
from django.core.exceptions import ValidationError

from apps.products.models import Product, Category, Brand, ProductUnitMeasurement


VALID_STATUSES = {
    "active":   Product.ProductStatus.ACTIVE,
    "inactive": Product.ProductStatus.INACTIVE,
    "draft":    Product.ProductStatus.DRAFT,
}

REQUIRED_COLUMNS = {"name", "category", "brand", "unit_measurement", "description", "status"}


class ProductImportService:

    @classmethod
    def import_from_excel(cls, file) -> dict:
        """
        Excel fayldan mahsulotlarni import qiladi.
        Qaytaradi: {"created": int, "skipped": int, "errors": [...]}
        """
        try:
            wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
        except Exception:
            raise ValidationError("Excel faylni o'qib bo'lmadi. Fayl .xlsx formatida bo'lishi kerak.")

        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))

        if not rows:
            raise ValidationError("Excel fayl bo'sh.")

        # Ustun nomlarini aniqlash
        raw_headers = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
        missing = REQUIRED_COLUMNS - set(raw_headers)
        if missing:
            raise ValidationError(f"Ustunlar topilmadi: {', '.join(missing)}")

        col = {name: idx for idx, name in enumerate(raw_headers)}
        data_rows = rows[1:]

        if not data_rows:
            raise ValidationError("Shablon bo'sh — ma'lumot qatorlari yo'q.")

        # ── Lookup map'larni oldindan tayyorlash (N+1 oldini olish) ──────────
        raw_categories   = {cls._cell(r, col["category"])   for r in data_rows}
        raw_brands       = {cls._cell(r, col["brand"])       for r in data_rows}
        raw_units        = {cls._cell(r, col["unit_measurement"]) for r in data_rows}

        raw_categories.discard("")
        raw_brands.discard("")
        raw_units.discard("")

        category_map = cls._build_category_map(raw_categories)
        brand_map    = cls._build_brand_map(raw_brands)
        unit_map     = cls._build_unit_map(raw_units)

        # ── Qatorlarni parse qilish ───────────────────────────────────────────
        to_create = []
        errors    = []

        for row_num, row in enumerate(data_rows, start=2):
            name = cls._cell(row, col["name"])

            if not name:
                errors.append({"row": row_num, "error": "name bo'sh — qator o'tkazib yuborildi"})
                continue

            status_raw = cls._cell(row, col["status"]).lower()
            status = VALID_STATUSES.get(status_raw, Product.ProductStatus.ACTIVE)

            unit_name = cls._cell(row, col["unit_measurement"]) or "dona"

            to_create.append(Product(
                name=name,
                category=category_map.get(cls._cell(row, col["category"]).lower()),
                brand=brand_map.get(cls._cell(row, col["brand"]).lower()),
                unit_measurement=unit_map.get(unit_name.lower()),
                description=cls._cell(row, col["description"]),
                status=status,
            ))

        if not to_create:
            return {"created": 0, "skipped": len(data_rows), "errors": errors}

        # ── Bulk create ───────────────────────────────────────────────────────
        # ignore_conflicts=False — xato bo'lsa butun batch xato beradi,
        # shuning uchun har birini alohida saqlaymiz (barcode unique bo'lgani uchun)
        created_count = 0
        with transaction.atomic():
            for idx, product in enumerate(to_create):
                try:
                    product.save()          # save() ichida barcode/sku generatsiya bo'ladi
                    created_count += 1
                except Exception as e:
                    # Excel qator raqamini hisoblash: header(1) + to_create indeksi
                    actual_row = 2 + idx
                    errors.append({"row": actual_row, "error": str(e)})

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
    def _build_category_map(names: set) -> dict:
        """Kategoriya nomlarini kichik harfda kalit sifatida qaytaradi."""
        if not names:
            return {}
        existing = Category.objects.filter(name__in=names)
        return {c.name.lower(): c for c in existing}

    @staticmethod
    def _build_brand_map(names: set) -> dict:
        if not names:
            return {}
        existing = Brand.objects.filter(name__in=names)
        return {b.name.lower(): b for b in existing}

    @staticmethod
    def _build_unit_map(names: set) -> dict:
        """
        Mavjud unit'larni oladi, yo'qlarini yaratadi.
        'dona' har doim mavjud bo'lishi kafolatlanadi.
        """
        names.add("dona")
        existing = ProductUnitMeasurement.objects.filter(measurement__in=names)
        unit_map = {u.measurement.lower(): u for u in existing}

        new_units = [
            ProductUnitMeasurement(measurement=name)
            for name in names
            if name.lower() not in unit_map
        ]
        if new_units:
            created = ProductUnitMeasurement.objects.bulk_create(new_units)
            for u in created:
                unit_map[u.measurement.lower()] = u

        return unit_map
