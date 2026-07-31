"""
Reports moduli endpointlari: meta (hisobot turlari + dinamik filtrlar),
generate (filtrlangan jadval, server-side pagination) va export (excel/csv —
generate bilan AYNAN bir xil filtrlar orqali).
"""
import csv
import io
from datetime import datetime

import xlsxwriter
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from apps.reports.permissions import scope_report_params
from apps.reports.services.report_builder import ReportBuilderService
from apps.contract.permissions import allowed_store_ids


def _scoped_meta(request) -> dict:
    """
    Meta — do'kon adminiga do'kon filtri variantlari o'z do'kon(lar)i bilan
    cheklab beriladi (superadmin hammasini ko'radi).
    """
    meta = ReportBuilderService.meta()
    allowed = allowed_store_ids(request.user)
    if allowed is None:
        return meta
    for report in meta["reports"]:
        for f in report["filters"]:
            if f["param"] == "store_id":
                f["options"] = [
                    o for o in f["options"]
                    if o["value"] != "all" and o["value"].isdigit() and int(o["value"]) in allowed
                ]
    return meta


@extend_schema(tags=["Reports"], summary="Hisobot turlari va dinamik filtrlar (meta)")
class ReportBuilderMetaAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_scoped_meta(request))


@extend_schema(
    tags=["Reports"],
    summary="Hisobot yaratish — tanlangan filtrlar bilan server-side jadval (pagination).",
)
class ReportBuilderGenerateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            # Do'kon admini faqat o'z do'koni bo'yicha (superadmin — istalgan/umumiy)
            data = ReportBuilderService.generate(scope_report_params(request))
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)
        return Response(data)


@extend_schema(
    tags=["Reports"],
    summary="Hisobotni yuklab olish (excel/csv) — jadval bilan AYNAN bir xil filtrlar.",
)
class ReportBuilderExportAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = scope_report_params(request)
        try:
            label, columns, rows, summary = ReportBuilderService.export_rows(params)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

        export_type = params.get("export_type", "excel")
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        report_type = params.get("report_type", "report")

        if export_type == "csv":
            # UTF-8 BOM — Excel kirillcha/lotincha matnni to'g'ri ochishi uchun
            response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
            response["Content-Disposition"] = f"attachment; filename={report_type}_{stamp}.csv"
            writer = csv.writer(response)
            writer.writerow([c["label"] for c in columns])
            for r in rows:
                writer.writerow([r.get(c["key"], "") for c in columns])
            # Summary pastda
            writer.writerow([])
            for s in summary:
                writer.writerow([s["label"], s["value"]])
            return response

        # Excel
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {"in_memory": True})
        # Varaq nomi 31 belgidan oshmasligi kerak; sarlavhadagi qavs ichidagi
        # izoh (masalan holat sanasi) faqat sarlavha satrida qoladi
        ws = wb.add_worksheet(label.split(" (")[0][:31] or "Hisobot")
        f_title = wb.add_format({"bold": True, "font_size": 13, "font_color": "#FFFFFF",
                                 "bg_color": "#0D366B", "valign": "vcenter", "indent": 1})
        f_meta = wb.add_format({"font_size": 9, "italic": True, "font_color": "#52514E"})
        f_head = wb.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#184F95",
                                "border": 1, "align": "center", "valign": "vcenter"})
        f_text = wb.add_format({"border": 1, "border_color": "#E1E0D9"})
        f_money = wb.add_format({"border": 1, "border_color": "#E1E0D9",
                                 "num_format": "#,##0.00", "align": "right"})
        f_int = wb.add_format({"border": 1, "border_color": "#E1E0D9",
                               "num_format": "#,##0", "align": "center"})
        f_sum_l = wb.add_format({"bold": True})
        f_sum_v = wb.add_format({"bold": True, "num_format": "#,##0.00"})

        last_col = len(columns) - 1
        ws.set_row(0, 26)
        ws.merge_range(0, 0, 0, max(last_col, 1), label, f_title)
        gen = datetime.now().strftime("%d.%m.%Y %H:%M")
        ws.merge_range(1, 0, 1, max(last_col, 1), f"Yaratildi: {gen}  |  Qatorlar: {len(rows)}", f_meta)

        for col, c in enumerate(columns):
            width = {"text": 28, "money": 16, "int": 12}.get(c["kind"], 16)
            ws.set_column(col, col, width)
            ws.write(3, col, c["label"], f_head)
        for i, r in enumerate(rows):
            for col, c in enumerate(columns):
                val = r.get(c["key"], "")
                if c["kind"] == "money":
                    try:
                        ws.write_number(4 + i, col, float(val), f_money)
                    except (TypeError, ValueError):
                        ws.write(4 + i, col, str(val), f_text)
                elif c["kind"] == "int":
                    try:
                        ws.write_number(4 + i, col, int(val), f_int)
                    except (TypeError, ValueError):
                        ws.write(4 + i, col, str(val), f_text)
                else:
                    ws.write(4 + i, col, "-" if val in (None, "") else str(val), f_text)

        # Summary bloki jadval ostida
        srow = 5 + len(rows)
        for s in summary:
            ws.write(srow, 0, s["label"], f_sum_l)
            try:
                ws.write_number(srow, 1, float(s["value"]), f_sum_v)
            except (TypeError, ValueError):
                ws.write(srow, 1, str(s["value"]), f_sum_v)
            srow += 1

        ws.freeze_panes(4, 0)
        if rows:
            ws.autofilter(3, 0, 3 + len(rows), last_col)
        wb.close()
        output.seek(0)

        response = HttpResponse(
            output,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f"attachment; filename={report_type}_{stamp}.xlsx"
        return response
