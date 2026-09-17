"""
Ro'yxatlarni Excel (.xlsx) ga eksport qilish uchun umumiy asos.

Har bir resurs (sotuv, kirim, mahsulot, ...) o'z app'ida `BaseExcelExportAPIView`
dan meros oladi va faqat uchta narsani belgilaydi: fayl nomi, queryset
(ro'yxat view'i bilan bir xil scoping!) va varaqlar (ustunlar + satrlar).

Sana oralig'i barcha eksportlarda bir xil: ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
"""

import io
import re
from datetime import date, datetime
from decimal import Decimal

import xlsxwriter
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import permissions
from rest_framework.views import APIView


def sanitize_worksheet_name(
    name: str | None,
    existing_names: set[str] | list[str] | None = None,
    default: str = "Hisobot",
    max_len: int = 31,
) -> str:
    """
    Sanitizes a worksheet name for Excel (xlsxwriter/openpyxl).

    Enforces all Excel worksheet naming constraints:
    - Forbidden characters [ ] : * ? / \\ are removed or converted to '-'
    - Leading/trailing whitespace, dashes, underscores, and apostrophes are stripped
    - Length is capped to max_len (default 31 characters, Excel hard limit)
    - Apostrophe at start or end is never allowed
    - Duplicate names in the workbook (case-insensitive) are suffixed with _1, _2, etc.
    - Fallback to `default` if sanitized name is empty
    """
    existing = {str(n).lower() for n in existing_names} if existing_names else set()
    raw = str(name if name is not None else "").strip()

    # Slashes and colons -> readable dash separator
    cleaned = raw.replace(" / ", " - ").replace(" \\ ", " - ")
    for ch in "/\\:":
        cleaned = cleaned.replace(ch, "-")

    # Brackets, asterisks, question marks -> remove
    for ch in "[]*?":
        cleaned = cleaned.replace(ch, "")

    # Normalize multiple whitespace and dashes
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s*-\s*", " - ", cleaned).strip()

    # Strip characters invalid at start/end (Excel forbids leading/trailing apostrophe)
    cleaned = cleaned.strip(" -_\t\r\n'")

    if not cleaned:
        cleaned = default

    cleaned = cleaned[:max_len].rstrip(" '")
    if not cleaned:
        cleaned = default[:max_len].rstrip(" '") or "Sheet"

    if cleaned.lower() not in existing:
        return cleaned

    counter = 1
    while True:
        suffix = f"_{counter}"
        base_cutoff = max_len - len(suffix)
        base_part = cleaned[:base_cutoff].rstrip(" '")
        if not base_part:
            base_part = default[:base_cutoff].rstrip(" '") or "Sheet"
        candidate = f"{base_part}{suffix}"
        if candidate.lower() not in existing:
            return candidate
        counter += 1


def safe_add_worksheet(workbook, name=None, default="Hisobot", max_len=31):
    """
    Safely adds a worksheet to an xlsxwriter Workbook, sanitizing forbidden Excel
    characters, enforcing the 31-character limit, and resolving any duplicate
    worksheet names.
    """
    existing_names = {ws.name.lower() for ws in workbook.worksheets()}
    safe_name = sanitize_worksheet_name(
        name, existing_names=existing_names, default=default, max_len=max_len
    )
    return workbook.add_worksheet(safe_name)


def parse_date_param(value):
    """'YYYY-MM-DD' → date; bo'sh yoki noto'g'ri format → None (filtr qo'llanmaydi)."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


class BaseExcelExportAPIView(APIView):
    """
    Subklass belgilaydi:
      filename    — fayl nomi prefiksi (sana avtomatik qo'shiladi)
      date_field  — sana filtri qo'llanadigan maydon (None bo'lsa filtrlanmaydi,
                    masalan filterset o'zi filtrlasa)
      get_queryset(request)          — eksport querysati
      get_sheets(request, queryset)  — [(varaq_nomi, ustunlar, satrlar), ...]
          ustunlar: [(sarlavha, kenglik), ...]
          satrlar:  iterably, har biri ustunlar tartibidagi qiymatlar ro'yxati
    """

    permission_classes = [permissions.IsAuthenticated]

    filename = "export"
    date_field = "created_at"

    def get_queryset(self, request):
        raise NotImplementedError

    def get_sheets(self, request, queryset):
        raise NotImplementedError

    def filter_by_date(self, request, queryset):
        if not self.date_field:
            return queryset
        date_from = parse_date_param(request.query_params.get("date_from"))
        date_to = parse_date_param(request.query_params.get("date_to"))
        if date_from:
            queryset = queryset.filter(**{f"{self.date_field}__date__gte": date_from})
        if date_to:
            queryset = queryset.filter(**{f"{self.date_field}__date__lte": date_to})
        return queryset

    @staticmethod
    def _write_cell(worksheet, row, col, value, dt_fmt):
        if value is None or value == "":
            return
        if isinstance(value, datetime):
            if timezone.is_aware(value):
                value = timezone.localtime(value).replace(tzinfo=None)
            worksheet.write_datetime(row, col, value, dt_fmt)
        elif isinstance(value, date):
            worksheet.write_datetime(row, col, value, dt_fmt)
        elif isinstance(value, Decimal):
            worksheet.write_number(row, col, float(value))
        else:
            worksheet.write(row, col, value)

    def get(self, request):
        queryset = self.filter_by_date(request, self.get_queryset(request))

        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
        header_fmt = workbook.add_format({
            "bold": True,
            "bg_color": "#DCE6F1",
            "border": 1,
            "valign": "vcenter",
            "text_wrap": True,
        })
        dt_fmt = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm"})

        for sheet_name, columns, rows in self.get_sheets(request, queryset):
            ws = safe_add_worksheet(workbook, sheet_name)
            for col_idx, (header, width) in enumerate(columns):
                ws.set_column(col_idx, col_idx, width)
                ws.write(0, col_idx, header, header_fmt)
            ws.freeze_panes(1, 0)
            for row_idx, row in enumerate(rows, start=1):
                for col_idx, value in enumerate(row):
                    self._write_cell(ws, row_idx, col_idx, value, dt_fmt)

        workbook.close()
        buffer.seek(0)

        stamp = timezone.localdate().strftime("%Y-%m-%d")
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{self.filename}_{stamp}.xlsx"'
        return response
