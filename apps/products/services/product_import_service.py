import openpyxl
from django.db import transaction
from django.db.models.functions import Lower
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
        # skipped — import QILINMAGAN qatorlar (sabab bilan)
        # warnings — import QILINGAN, lekin e'tibor talab qiluvchi qatorlar (kategoriya/brend topilmadi)
        to_create = []
        skipped   = []
        warnings  = []
        seen_barcodes = set()
        seen_skus     = set()

        for row_num, row in enumerate(data_rows, start=2):
            name = cls._cell(row, col["name"])
            if not name:
                skipped.append({"row": row_num, "reason": "name bo'sh"})
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
                        skipped.append({"row": row_num, "reason": "barcode yaroqsiz (faqat 12-13 raqamli EAN-13)"})
                        continue
                    if barcode_val in existing_barcodes or barcode_val in seen_barcodes:
                        skipped.append({"row": row_num, "reason": f"barcode takrorlangan: {barcode_val}"})
                        continue
                    seen_barcodes.add(barcode_val)

            # SKU: kelsa takror tekshiriladi, kelmasa None (avtomatik)
            sku_val = None
            if has_sku:
                raw_sku = cls._cell(row, col["sku"])
                if raw_sku:
                    if raw_sku in existing_skus or raw_sku in seen_skus:
                        skipped.append({"row": row_num, "reason": f"sku takrorlangan: {raw_sku}"})
                        continue
                    seen_skus.add(raw_sku)
                    sku_val = raw_sku

            # KATEGORIYA / BREND: topilmasa qator BARIBIR import qilinadi (None bilan),
            # lekin foydalanuvchi bilishi uchun warnings ro'yxatiga yoziladi.
            cat_raw = cls._cell(row, col["category"])
            category = category_map.get(cat_raw.lower()) if cat_raw else None
            if cat_raw and category is None:
                warnings.append({"row": row_num, "error": f"kategoriya topilmadi: '{cat_raw}' — kategoriyasiz saqlandi"})

            brand_raw = cls._cell(row, col["brand"])
            brand = brand_map.get(brand_raw.lower()) if brand_raw else None
            if brand_raw and brand is None:
                warnings.append({"row": row_num, "error": f"brend topilmadi: '{brand_raw}' — brendsiz saqlandi"})

            to_create.append((row_num, Product(
                name=name,
                category=category,
                brand=brand,
                unit_measurement=unit_map.get(unit_name.lower()),
                description=cls._cell(row, col["description"]),
                status=status,
                min_stock=min_stock,
                barcode=barcode_val,
                sku=sku_val,
            )))

        # Har birini ALOHIDA savepoint ichida save() —
        #   1) barcode/sku generatsiya bo'lishi uchun (model.save() ichida)
        #   2) bitta qatordagi xato (masalan dublikat) butun importni bekor qilmasligi uchun.
        #      transaction.atomic() bloki ichida DB xatosidan keyin transaction "buziladi",
        #      shuning uchun har bir save() o'zining savepoint'iga o'raladi.
        # ⚠️ MUAMMO [PERF]: Katta importda (masalan qoldiq faylidan ~12.5k mahsulot) bu sikl
        # har qatorga ALOHIDA INSERT + alohida savepoint (BEGIN/RELEASE) yuboradi.
        # Natija: ~N ta round-trip DB'ga → import bir necha daqiqa cho'zilishi va so'rov davomida
        # bloklanish (ayniqsa import HTTP request ichida sinxron bajarilsa) mumkin.
        # Sabab tushunarli: barcode/sku model.save() ichida generatsiya bo'ladi va bitta dublikat
        # butun importни buzmasligi kerak. Lekin ikkalasini ham bulk yo'l bilan yechish mumkin.
        # ✅ YECHIM (production):
        #   1) barcode/sku'ni save()'dan tashqarida, sikldan oldin generatsiya qilib obyektga o'rnatish
        #      (yoki DB default/sekvensiya), so'ng bloklarga bo'lib bulk_create ishlatish:
        #        Product.objects.bulk_create(chunk, batch_size=500, ignore_conflicts=True)
        #      ignore_conflicts unique dublikatlarни butun importни buzmasdan o'tkazib yuboradi.
        #   2) Xato qatorlarni aniq hisoblash kerak bo'lsa: avval mavjud barcode/sku'larni bitta
        #      query bilan ajratib (yuqorida allaqachon bor _existing_* metodlari), toza qatorlarni
        #      bulk_create qilish → dublikatlar allaqachon "skipped"ga tushadi, per-row try/except shart emas.
        #   3) Import og'ir bo'lsa — uni Celery/async task'ga ko'chirib, HTTP request'ni bloklamaslik.
        created_count = 0
        for row_num, product in to_create:
            try:
                with transaction.atomic():
                    product.save()
                created_count += 1
            except Exception as e:
                skipped.append({"row": row_num, "reason": cls._humanize_db_error(e)})

        return {
            "created": created_count,
            "skipped": skipped,
            "errors":  warnings,
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
    def _humanize_db_error(exc: Exception) -> str:
        text = str(exc).lower()
        if "barcode" in text and "unique" in text:
            return "barcode bazada mavjud (dublikat)"
        if "sku" in text and "unique" in text:
            return "sku bazada mavjud (dublikat)"
        if "unique" in text:
            return "takrorlanuvchi qiymat (dublikat)"
        return f"saqlashda xato: {exc}"

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
        lowered = {n.lower() for n in names if n}
        if not lowered:
            return {}
        # Case-insensitive moslik: Lower("name") asosida solishtiramiz.
        qs = Category.objects.annotate(_lname=Lower("name")).filter(_lname__in=lowered)
        return {c.name.lower(): c for c in qs}

    @staticmethod
    def _build_brand_map(names: set) -> dict:
        lowered = {n.lower() for n in names if n}
        if not lowered:
            return {}
        qs = Brand.objects.annotate(_lname=Lower("name")).filter(_lname__in=lowered)
        return {b.name.lower(): b for b in qs}

    @staticmethod
    def _build_unit_map(names: set) -> dict:
        names = {n for n in names if n}
        names.add("dona")
        lowered = {n.lower() for n in names}

        existing = ProductUnitMeasurement.objects.annotate(
            _lname=Lower("measurement")
        ).filter(_lname__in=lowered)
        unit_map = {u.measurement.lower(): u for u in existing}

        # Yangi o'lchov birliklarini lower bo'yicha unikal qilib yaratamiz.
        seen = set(unit_map.keys())
        new_units = []
        for name in names:
            key = name.lower()
            if key not in seen:
                seen.add(key)
                new_units.append(ProductUnitMeasurement(measurement=name))

        if new_units:
            for u in ProductUnitMeasurement.objects.bulk_create(new_units):
                unit_map[u.measurement.lower()] = u

        return unit_map


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1  (yakuniy save() sikli — har qatorga alohida INSERT + savepoint)
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10
# Izoh: ✅ YAXSHI — kategoriya/brend/birlik map'lari va mavjud barcode/sku bulk query bilan
#   oldindan olinadi (N+1 lookup yo'q). Yagona zaif joy — yakuniy per-row save() sikli.
# Prioritet bo'yicha birinchi hal qilinishi kerak: [import'ni bulk_create(batch_size, ignore_conflicts) ga o'tkazish; og'ir importni async task'ga]
# ═══════════════════════════════