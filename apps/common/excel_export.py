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


def sanitize_table_name(
    name: str | None,
    existing_names: set[str] | list[str] | None = None,
    default: str = "ReportTable",
    max_len: int = 31,
) -> str:
    """
    Sanitizes an Excel OpenXML Table name.

    Constraints:
    - Must start with a letter or underscore
    - Contains only alphanumeric characters and underscores [A-Za-z0-9_]
    - Must be unique within the workbook (case-insensitive)
    - Capped at max_len (default 31)
    """
    existing = {str(n).lower() for n in existing_names} if existing_names else set()
    raw = str(name if name is not None else "").strip()
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch == "_")
    if not cleaned:
        cleaned = default
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"T_{cleaned}"
    cleaned = cleaned[:max_len]

    if cleaned.lower() not in existing:
        return cleaned

    counter = 1
    while True:
        suffix = f"_{counter}"
        base_cutoff = max_len - len(suffix)
        candidate = f"{cleaned[:base_cutoff]}{suffix}"
        if candidate.lower() not in existing:
            return candidate
        counter += 1


def calculate_optimal_column_widths(
    columns: list[dict] | list[tuple],
    rows: list[dict] | list[list] | list[tuple],
    padding: int = 3,
) -> dict[int, int]:
    """
    Calculates content-aware, balanced column widths for Excel sheets:
    - Considers header length (+ padding for AutoFilter arrow)
    - Considers length of data values across all rows
    - Applies category-specific min_width and safe max_width boundaries
    - Safely caps long text columns (names, descriptions, comments) to avoid stretched sheets
    - Keeps SKU, barcode, IDs, quantities, prices, dates compact and neat
    """
    col_widths = {}
    for col_idx, col in enumerate(columns):
        if isinstance(col, dict):
            key = str(col.get("key", "")).lower()
            label = str(col.get("label") or col.get("header") or "")
            kind = str(col.get("kind", "")).lower()
        elif isinstance(col, (list, tuple)):
            label = str(col[0] or "")
            key = ""
            kind = ""
        else:
            label = str(col or "")
            key = ""
            kind = ""

        label_lower = label.lower()

        # Type-specific bounds for compact vs content columns
        if kind == "money" or any(k in key or k in label_lower for k in ("price", "narx", "tushum", "revenue", "profit", "foyda", "summa", "amount", "debt", "qarz", "cost", "tannarx", "total")):
            min_w, max_w = 12, 20
        elif kind == "int" or any(k in key or k in label_lower for k in ("id", "№", "chek", "rank", "soni", "count", "buyurtma", "sessiya")):
            min_w, max_w = 8, 14
        elif kind in ("number", "qty") or any(k in key or k in label_lower for k in ("qty", "miqdor", "stock", "qoldiq", "sold", "sotilgan", "tafovut")):
            min_w, max_w = 9, 16
        elif kind == "pct" or any(k in key or k in label_lower for k in ("percent", "ulush", "foiz", "share", "rate")):
            min_w, max_w = 8, 12
        elif kind in ("date", "datetime") or any(k in key or k in label_lower for k in ("sana", "date", "vaqt", "time")):
            min_w, max_w = 12, 19
        elif any(k in key or k in label_lower for k in ("sku", "barcode", "shtrix", "phone", "telefon")):
            min_w, max_w = 12, 22
        elif any(k in key or k in label_lower for k in ("status", "holat", "badge", "payment", "to'lov")):
            min_w, max_w = 10, 18
        else:
            # Text columns (product name, description, category, brand, supplier, comments)
            min_w, max_w = 12, 40

        max_len = len(label) + padding

        for row in rows:
            if isinstance(row, dict):
                val = row.get(key)
            elif isinstance(row, (list, tuple)) and col_idx < len(row):
                val = row[col_idx]
            else:
                val = None

            if val is None or val == "":
                v_len = 1
            elif isinstance(val, (datetime, date)):
                v_len = 16 if isinstance(val, datetime) else 10
            elif isinstance(val, Decimal):
                v_len = len(f"{float(val):,.2f}") if kind == "money" else len(f"{float(val):,.2f}".rstrip("0").rstrip("."))
            elif isinstance(val, float):
                v_len = len(f"{val:,.2f}")
            elif isinstance(val, int):
                v_len = len(f"{val:,}")
            else:
                v_str = str(val).strip()
                lines = v_str.split("\n")
                v_len = max(len(ln) for ln in lines) if lines else len(v_str)

            if v_len + 2 > max_len:
                max_len = v_len + 2
                if max_len >= max_w:
                    max_len = max_w
                    break

        col_widths[col_idx] = max(min_w, min(max_w, max_len))

    return col_widths


def get_report_excel_formats(workbook: xlsxwriter.Workbook) -> dict:
    """
    Standard clean & neutral formatting palette for all CRM Excel exports:
    - Pure white data rows (#FFFFFF)
    - Very soft, neutral borders (#E2E8F0)
    - Legible header with subtle neutral fill (#F8FAFC) and dark slate text (#0F172A)
    - Zero banded rows / zero dark row fills
    """
    border_color = "#E2E8F0"
    header_border_color = "#CBD5E1"
    text_color = "#1E293B"
    header_text_color = "#0F172A"

    def _f(**props):
        base = {
            "font_name": "Calibri",
            "font_size": 10,
            "font_color": text_color,
        }
        base.update(props)
        return workbook.add_format(base)

    return {
        "title": _f(
            font_size=13,
            bold=True,
            font_color="#0F172A",
            valign="vcenter",
        ),
        "meta": _f(
            font_size=9,
            italic=True,
            font_color="#64748B",
            valign="vcenter",
        ),
        "card_title": _f(
            font_size=11,
            bold=True,
            font_color="#0F172A",
            valign="vcenter",
        ),
        "header": _f(
            bold=True,
            font_color=header_text_color,
            bg_color="#F8FAFC",
            border=1,
            border_color=header_border_color,
            align="center",
            valign="vcenter",
            text_wrap=True,
        ),
        "text": _f(
            border=1,
            border_color=border_color,
            bg_color="#FFFFFF",
            valign="vcenter",
            text_wrap=True,
        ),
        "money": _f(
            border=1,
            border_color=border_color,
            bg_color="#FFFFFF",
            num_format="#,##0.00",
            align="right",
            valign="vcenter",
        ),
        "int": _f(
            border=1,
            border_color=border_color,
            bg_color="#FFFFFF",
            num_format="#,##0",
            align="center",
            valign="vcenter",
        ),
        "qty": _f(
            border=1,
            border_color=border_color,
            bg_color="#FFFFFF",
            num_format="#,##0.00",
            align="right",
            valign="vcenter",
        ),
        "pct": _f(
            border=1,
            border_color=border_color,
            bg_color="#FFFFFF",
            num_format='0.0"%"',
            align="center",
            valign="vcenter",
        ),
        "date": _f(
            border=1,
            border_color=border_color,
            bg_color="#FFFFFF",
            num_format="yyyy-mm-dd",
            align="center",
            valign="vcenter",
        ),
        "datetime": _f(
            border=1,
            border_color=border_color,
            bg_color="#FFFFFF",
            num_format="yyyy-mm-dd hh:mm",
            align="center",
            valign="vcenter",
        ),
        "sum_label": _f(
            bold=True,
            font_color="#334155",
            bg_color="#F8FAFC",
            border=1,
            border_color=border_color,
            align="left",
            valign="vcenter",
        ),
        "sum_val": _f(
            bold=True,
            font_color="#0F172A",
            bg_color="#FFFFFF",
            border=1,
            border_color=border_color,
            align="right",
            valign="vcenter",
            num_format="#,##0.00",
        ),
    }


def write_report_table(
    worksheet,
    head_row: int,
    columns: list[dict],
    rows: list[dict] | list[list],
    table_name: str,
    formats: dict,
    freeze_panes: bool = True,
) -> int:
    """
    Renders a uniform, beautifully formatted Excel table for reports:
    - Calculates and sets optimal content-aware column widths
    - Applies minimal Excel Table with AutoFilter and WITHOUT banded rows/stripes
    - Writes all rows with white background and thin neutral borders
    - Preserves data types (money, int, quantity, date, text)
    - Freezes panes right below the header row
    - Returns the last data row index
    """
    # 1. Optimal column widths
    col_widths = calculate_optimal_column_widths(columns, rows)
    for col_idx, width in col_widths.items():
        worksheet.set_column(col_idx, col_idx, width)

    # 2. Header row height
    worksheet.set_row(head_row, 24)

    # 3. Excel Table configuration
    last_col = len(columns) - 1
    table_last_row = head_row + max(len(rows), 1)
    safe_tbl_name = sanitize_table_name(table_name)
    table_cols = [
        {
            "header": str(c.get("label") or c.get("header") or f"Col_{idx+1}"),
            "header_format": formats["header"],
        }
        for idx, c in enumerate(columns)
    ]

    worksheet.add_table(
        head_row,
        0,
        table_last_row,
        last_col,
        {
            "name": safe_tbl_name,
            "columns": table_cols,
            "style": "Table Style Light 1",
            "banded_rows": False,
            "banded_columns": False,
            "autofilter": True,
        },
    )

    first_data_row = head_row + 1

    # 4. Write data rows
    for i, row in enumerate(rows):
        r_idx = first_data_row + i
        for col_idx, col in enumerate(columns):
            key = col.get("key")
            kind = col.get("kind", "text")
            val = row.get(key) if isinstance(row, dict) else (row[col_idx] if col_idx < len(row) else None)

            if val is None or val == "":
                worksheet.write(r_idx, col_idx, "-", formats["text"])
            elif kind == "money":
                try:
                    worksheet.write_number(r_idx, col_idx, float(val), formats["money"])
                except (TypeError, ValueError):
                    worksheet.write(r_idx, col_idx, str(val), formats["text"])
            elif kind == "int":
                try:
                    worksheet.write_number(r_idx, col_idx, int(val), formats["int"])
                except (TypeError, ValueError):
                    worksheet.write(r_idx, col_idx, str(val), formats["text"])
            elif kind in ("number", "qty"):
                try:
                    worksheet.write_number(r_idx, col_idx, float(val), formats["qty"])
                except (TypeError, ValueError):
                    worksheet.write(r_idx, col_idx, str(val), formats["text"])
            elif kind == "pct":
                try:
                    worksheet.write_number(r_idx, col_idx, float(str(val).replace("%", "").strip()), formats["pct"])
                except (TypeError, ValueError):
                    worksheet.write(r_idx, col_idx, str(val), formats["text"])
            elif isinstance(val, datetime):
                if timezone.is_aware(val):
                    val = timezone.localtime(val).replace(tzinfo=None)
                worksheet.write_datetime(r_idx, col_idx, val, formats["datetime"])
            elif isinstance(val, date):
                worksheet.write_datetime(r_idx, col_idx, val, formats["date"])
            else:
                worksheet.write(r_idx, col_idx, str(val), formats["text"])

    # 5. Freeze panes below header
    if freeze_panes:
        worksheet.freeze_panes(first_data_row, 0)

    return table_last_row


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
    def _write_cell(worksheet, row, col, value, dt_fmt, text_fmt, num_fmt):
        if value is None or value == "":
            worksheet.write(row, col, "-", text_fmt)
            return
        if isinstance(value, datetime):
            if timezone.is_aware(value):
                value = timezone.localtime(value).replace(tzinfo=None)
            worksheet.write_datetime(row, col, value, dt_fmt)
        elif isinstance(value, date):
            worksheet.write_datetime(row, col, value, dt_fmt)
        elif isinstance(value, Decimal):
            worksheet.write_number(row, col, float(value), num_fmt)
        elif isinstance(value, (int, float)):
            worksheet.write_number(row, col, value, num_fmt)
        else:
            worksheet.write(row, col, str(value), text_fmt)

    def get(self, request):
        queryset = self.filter_by_date(request, self.get_queryset(request))

        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
        fmts = get_report_excel_formats(workbook)
        header_fmt = fmts["header"]
        dt_fmt = fmts["datetime"]
        text_fmt = fmts["text"]
        num_fmt = fmts["money"]

        for sheet_name, columns, rows in self.get_sheets(request, queryset):
            ws = safe_add_worksheet(workbook, sheet_name)
            rows_list = list(rows)
            # Calculate optimal column widths
            col_widths = calculate_optimal_column_widths(columns, rows_list)
            ws.set_row(0, 24)
            for col_idx, col_spec in enumerate(columns):
                width = col_widths.get(col_idx, 16)
                header = col_spec[0] if isinstance(col_spec, (list, tuple)) else str(col_spec)
                ws.set_column(col_idx, col_idx, width)
                ws.write(0, col_idx, header, header_fmt)
            ws.freeze_panes(1, 0)
            for row_idx, row in enumerate(rows_list, start=1):
                for col_idx, value in enumerate(row):
                    self._write_cell(ws, row_idx, col_idx, value, dt_fmt, text_fmt, num_fmt)

        workbook.close()
        buffer.seek(0)

        stamp = timezone.localdate().strftime("%Y-%m-%d")
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{self.filename}_{stamp}.xlsx"'
        return response
