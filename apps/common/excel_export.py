"""
Ro'yxatlarni Excel (.xlsx) ga eksport qilish uchun umumiy asos.

Har bir resurs (sotuv, kirim, mahsulot, ...) o'z app'ida `BaseExcelExportAPIView`
dan meros oladi va faqat uchta narsani belgilaydi: fayl nomi, queryset
(ro'yxat view'i bilan bir xil scoping!) va varaqlar (ustunlar + satrlar).

Sana oralig'i barcha eksportlarda bir xil: ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
"""

import io
from datetime import date, datetime
from decimal import Decimal

import xlsxwriter
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import permissions
from rest_framework.views import APIView


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
            ws = workbook.add_worksheet(str(sheet_name)[:31])
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
