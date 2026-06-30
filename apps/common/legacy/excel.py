"""
Eski CRM Excel hisobotlarini xavfsiz o'qish uchun yordamchilar.

Muhim nuqtalar:
  * Bu fayllar `read_only` rejimda noto'g'ri o'lcham (dimension) qaytaradi —
    `ws.reset_dimensions()` chaqirilmasa openpyxl ustunlarni kesib tashlaydi.
  * Sana ustunlari ba'zan matn ("2026-06-29 11:59:05"), ba'zan haqiqiy
    datetime/date bo'lib keladi — `parse_dt` ikkalasini ham qo'llaydi.
  * Barcha sonlar float bo'lib keladi (masalan 2.0) — Decimal'ga `str()`
    orqali o'tkazamiz (suzuvchi nuqta xatosini oldini olish uchun).
"""
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
from django.utils import timezone


class LegacySheet:
    """
    Bitta Excel faylning aktiv varag'ini ustun-nom bo'yicha o'qiydi.

    Foydalanish:
        sheet = LegacySheet.open(path, OSTATKI_COLUMNS)
        for row in sheet.rows():
            row.text("name"), row.dec("purchase_price"), row.dt("last_import")
    """

    def __init__(self, workbook, worksheet, index):
        self._wb = workbook
        self._ws = worksheet
        self._index = index          # {logical_key: column_index}

    @classmethod
    def open(cls, path, column_map):
        """
        column_map: {logical_key: [bo'lishi mumkin bo'lgan sarlavhalar]}.
        Sarlavha qatori o'qilib, har bir kalit uchun ustun indeksi aniqlanadi.
        Topilmagan kalitlar uchun ValueError ko'tariladi.
        """
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        ws.reset_dimensions()  # read_only buzuq o'lchamni tuzatish

        row_iter = ws.iter_rows(values_only=True)
        try:
            header = next(row_iter)
        except StopIteration:
            wb.close()
            raise ValueError(f"Fayl bo'sh: {Path(path).name}")

        normalized = [str(h).strip() if h is not None else "" for h in header]

        index = {}
        missing = []
        for key, candidates in column_map.items():
            found = next((normalized.index(c) for c in candidates if c in normalized), None)
            if found is None:
                missing.append(f"{key} ({' / '.join(candidates)})")
            else:
                index[key] = found

        if missing:
            wb.close()
            raise ValueError(
                f"{Path(path).name}: ustun(lar) topilmadi: {', '.join(missing)}"
            )

        sheet = cls(wb, ws, index)
        sheet._row_iter = row_iter  # sarlavhadan keyingi qatorlar
        return sheet

    def rows(self):
        """Sarlavhadan keyingi har bir qatorni `LegacyRow` sifatida qaytaradi."""
        try:
            for raw in self._row_iter:
                yield LegacyRow(raw, self._index)
        finally:
            self._wb.close()


class LegacyRow:
    """Bitta qator — ustunlarga mantiqiy kalit orqali kirish + tip konvertatsiyasi."""

    __slots__ = ("_raw", "_index")

    def __init__(self, raw, index):
        self._raw = raw
        self._index = index

    def _value(self, key):
        idx = self._index.get(key)
        if idx is None or idx >= len(self._raw):
            return None
        return self._raw[idx]

    def text(self, key) -> str:
        """Bo'sh -> "" (None hech qachon qaytmaydi)."""
        val = self._value(key)
        if val is None:
            return ""
        return str(val).strip()

    def dec(self, key, default="0") -> Decimal:
        """Decimal — narx/summalar uchun. Yaroqsiz -> default."""
        val = self._value(key)
        if val is None or val == "":
            return Decimal(default)
        try:
            return Decimal(str(val))
        except (InvalidOperation, ValueError, TypeError):
            return Decimal(default)

    def qty(self, key, default=0) -> int:
        """Manfiy bo'lmagan butun son (miqdor uchun). Yaroqsiz/manfiy -> max(0, ...)."""
        val = self._value(key)
        if val is None or val == "":
            return default
        try:
            return max(int(round(float(val))), 0)
        except (ValueError, TypeError):
            return default

    def dt(self, key):
        """
        Sana/vaqtni timezone-aware datetime'ga aylantiradi.
        Matn ("YYYY-MM-DD[ HH:MM:SS]") yoki datetime/date qabul qiladi.
        Bo'sh/yaroqsiz -> None.
        """
        val = self._value(key)
        if val is None or val == "":
            return None

        if isinstance(val, datetime):
            parsed = val
        elif isinstance(val, date):
            parsed = datetime(val.year, val.month, val.day)
        else:
            parsed = _parse_dt_str(str(val).strip())
            if parsed is None:
                return None

        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed


_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y",
)


def _parse_dt_str(text: str):
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def find_file(docs_dir: Path, pattern: str) -> Path:
    """`docs/` ichidan glob bo'yicha yagona faylni topadi. Topilmasa/ko'p bo'lsa xato."""
    matches = sorted(docs_dir.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"Fayl topilmadi: {pattern} ({docs_dir})")
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        raise FileNotFoundError(f"Bir nechta fayl mos keldi ({pattern}): {names}")
    return matches[0]
