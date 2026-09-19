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
from apps.users.permissions import user_has_perm
from apps.common.excel_export import (
    get_report_excel_formats,
    safe_add_worksheet,
    sanitize_table_name,
    sanitize_worksheet_name,
    write_report_table,
)


def _scoped_meta(request) -> dict:
    """
    Meta — foydalanuvchiga faqat ruxsat berilgan hisobotlar va
    o'z do'kon(lar)i variantlari beriladi.
    """
    user = request.user
    if not user_has_perm(user, "reports.view"):
        return {"reports": []}

    meta = ReportBuilderService.meta()
    allowed_stores = allowed_store_ids(user)

    filtered_reports = []
    for report in meta["reports"]:
        rep_key = report["key"]
        has_view = user_has_perm(user, f"reports.{rep_key}.view")
        if not has_view and rep_key in ("sales_by_product", "product_efficiency", "abc_analysis", "order_returns"):
            has_view = user_has_perm(user, "reports.sales.view") or user_has_perm(user, "sales.view")
        if not has_view and rep_key == "inventory_results":
            has_view = user_has_perm(user, "reports.inventory.view") or user_has_perm(user, "inventory.view")
        if not has_view and rep_key == "write_offs":
            has_view = (
                user_has_perm(user, "reports.inventory.view")
                or user_has_perm(user, "inventory.view")
                or user_has_perm(user, "writeoff.view")
            )
        if not has_view and rep_key == "imports":
            has_view = (
                user_has_perm(user, "reports.inventory.view")
                or user_has_perm(user, "inventory.view")
                or user_has_perm(user, "stockentry.view")
            )
        if not has_view:
            continue

        if allowed_stores is not None:
            for f in report["filters"]:
                if f["param"] == "store_id":
                    f["options"] = [
                        o for o in f["options"]
                        if o["value"] != "all" and o["value"].isdigit() and int(o["value"]) in allowed_stores
                    ]
        filtered_reports.append(report)

    return {"reports": filtered_reports}


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
        report_type = request.query_params.get("report_type")
        if not report_type:
            return Response({"report_type": "Hisobot turi ko'rsatilmadi"}, status=status.HTTP_400_BAD_REQUEST)

        if not user_has_perm(request.user, "reports.view"):
            return Response(
                {"detail": "Sizda hisobotlar moduliga kirish huquqi yo'q.", "permission": "reports.view"},
                status=status.HTTP_403_FORBIDDEN,
            )

        req_perm = f"reports.{report_type}.view"
        has_perm = user_has_perm(request.user, req_perm)
        if not has_perm and report_type in ("sales_by_product", "product_efficiency", "abc_analysis", "order_returns"):
            has_perm = user_has_perm(request.user, "reports.sales.view") or user_has_perm(request.user, "sales.view")
        if not has_perm and report_type == "inventory_results":
            has_perm = user_has_perm(request.user, "reports.inventory.view") or user_has_perm(request.user, "inventory.view")
        if not has_perm and report_type == "write_offs":
            has_perm = (
                user_has_perm(request.user, "reports.inventory.view")
                or user_has_perm(request.user, "inventory.view")
                or user_has_perm(request.user, "writeoff.view")
            )
        if not has_perm and report_type == "imports":
            has_perm = (
                user_has_perm(request.user, "reports.inventory.view")
                or user_has_perm(request.user, "inventory.view")
                or user_has_perm(request.user, "stockentry.view")
            )
        if not has_perm:
            return Response(
                {
                    "detail": f"Sizda «{report_type}» hisobotini ko'rish huquqi yo'q.",
                    "permission": req_perm,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            # Do'kon admini faqat o'z do'koni bo'yicha (superadmin — istalgan/umumiy)
            data = ReportBuilderService.generate(scope_report_params(request), request.user)
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
        report_type = params.get("report_type")
        if not report_type:
            return Response({"report_type": "Hisobot turi ko'rsatilmadi"}, status=status.HTTP_400_BAD_REQUEST)

        if not user_has_perm(request.user, "reports.view"):
            return Response(
                {"detail": "Sizda hisobotlar moduliga kirish huquqi yo'q.", "permission": "reports.view"},
                status=status.HTTP_403_FORBIDDEN,
            )

        req_perm = f"reports.{report_type}.export"
        has_perm = user_has_perm(request.user, req_perm)
        if not has_perm and report_type in ("sales_by_product", "product_efficiency", "abc_analysis", "order_returns"):
            has_perm = (
                user_has_perm(request.user, "reports.sales.export")
                or user_has_perm(request.user, "reports.sales.view")
                or user_has_perm(request.user, "sales.export")
                or user_has_perm(request.user, "sales.view")
            )
        if not has_perm and report_type == "inventory_results":
            has_perm = (
                user_has_perm(request.user, "reports.inventory.export")
                or user_has_perm(request.user, "reports.inventory.view")
                or user_has_perm(request.user, "inventory.export")
                or user_has_perm(request.user, "inventory.view")
            )
        if not has_perm and report_type == "write_offs":
            has_perm = (
                user_has_perm(request.user, "reports.write_offs.view")
                or user_has_perm(request.user, "reports.inventory.export")
                or user_has_perm(request.user, "inventory.export")
                or user_has_perm(request.user, "writeoff.view")
            )
        if not has_perm and report_type == "imports":
            has_perm = (
                user_has_perm(request.user, "reports.inventory.export")
                or user_has_perm(request.user, "inventory.export")
                or user_has_perm(request.user, "stockentry.export")
            )
        if not has_perm:
            return Response(
                {
                    "detail": f"Sizda «{report_type}» hisobotini eksport qilish (yuklab olish) huquqi yo'q.",
                    "permission": req_perm,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            label, columns, rows, summary, info = ReportBuilderService.export_rows(params, request.user)
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
            if report_type in ("supplier_sales", "sales"):
                writer.writerow([c["label"] for c in columns])
                for r in rows:
                    writer.writerow([r.get(c["key"], "") for c in columns])
                return response

            # Kartochka (mahsulot tafsilotlari) jadval tepasida — fayl o'zi
            # yetarli bo'lishi uchun (qaysi mahsulot ekani ko'rinib tursin)
            if info:
                writer.writerow([info.get("title", "")])
                if info.get("subtitle"):
                    writer.writerow([info["subtitle"]])
                for field in info.get("fields", []):
                    writer.writerow([field["label"], field["value"]])
                writer.writerow([])
            writer.writerow([c["label"] for c in columns])
            for r in rows:
                writer.writerow([r.get(c["key"], "") for c in columns])
            # Summary pastda
            writer.writerow([])
            for s in summary:
                writer.writerow([s["label"], s["value"]])
            return response

        elif export_type == "pdf":
            from apps.reports.services.report_pdf_service import ReportPdfService
            pdf_bytes = ReportPdfService.render(label, columns, rows, summary, info, params)
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{report_type}_{stamp}.pdf"'
            return response

        # Excel
        if report_type == "sales":
            # Sheet 1: Cheklar (view="receipts")
            params_receipts = params.copy()
            params_receipts["view"] = "receipts"
            label1, cols1, rows1, summary1, _ = ReportBuilderService.export_rows(params_receipts, request.user)

            # Sheet 2: Mahsulotlar (view="items")
            params_items = params.copy()
            params_items["view"] = "items"
            label2, cols2, rows2, summary2, _ = ReportBuilderService.export_rows(params_items, request.user)

            output = io.BytesIO()
            wb = xlsxwriter.Workbook(output, {"in_memory": True})
            fmts = get_report_excel_formats(wb)

            ws1 = safe_add_worksheet(wb, "Cheklar")
            write_report_table(ws1, 0, cols1, rows1, "CheklarTable", fmts)

            ws2 = safe_add_worksheet(wb, "Mahsulotlar")
            write_report_table(ws2, 0, cols2, rows2, "MahsulotlarTable", fmts)

            wb.close()
            output.seek(0)

            response = HttpResponse(
                output,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = f"attachment; filename=Sales_Report_{stamp}.xlsx"
            return response

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {"in_memory": True})
        fmts = get_report_excel_formats(wb)
        ws = safe_add_worksheet(wb, label.split(" (")[0] if label else "Hisobot", default="Hisobot")

        if report_type == "supplier_sales":
            write_report_table(ws, 0, columns, rows, "SupplierSalesTable", fmts)
            wb.close()
            output.seek(0)

            response = HttpResponse(
                output,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = f"attachment; filename={report_type}_{stamp}.xlsx"
            return response

        last_col = len(columns) - 1
        ws.set_row(0, 24)
        ws.merge_range(0, 0, 0, max(last_col, 1), label, fmts["title"])
        gen = datetime.now().strftime("%d.%m.%Y %H:%M")
        ws.merge_range(1, 0, 1, max(last_col, 1), f"Yaratildi: {gen}  |  Qatorlar: {len(rows)}", fmts["meta"])

        # Kartochka bloki (mahsulot tafsilotlari) — jadval sarlavhasidan tepada.
        head_row = 3
        if info:
            cursor = 3
            ws.write(cursor, 0, info.get("title", ""), fmts["card_title"])
            cursor += 1
            if info.get("subtitle"):
                ws.write(cursor, 0, info["subtitle"], fmts["meta"])
                cursor += 1
            for field in info.get("fields", []):
                ws.write(cursor, 0, field["label"], fmts["sum_label"])
                value = field.get("value")
                if field.get("kind") in ("money", "int"):
                    try:
                        ws.write_number(cursor, 1, float(value), fmts["sum_val"])
                    except (TypeError, ValueError):
                        ws.write(cursor, 1, str(value), fmts["text"])
                else:
                    ws.write(cursor, 1, "-" if value in (None, "") else str(value), fmts["text"])
                cursor += 1
            head_row = cursor + 1

        clean_name = sanitize_table_name("".join(ch for ch in report_type.title() if ch.isalnum()) + "Table")
        table_last_row = write_report_table(ws, head_row, columns, rows, clean_name, fmts)

        # Summary bloki jadval ostida
        srow = table_last_row + 2
        for s in summary:
            ws.write(srow, 0, s["label"], fmts["sum_label"])
            try:
                ws.write_number(srow, 1, float(s["value"]), fmts["sum_val"])
            except (TypeError, ValueError):
                ws.write(srow, 1, str(s["value"]), fmts["sum_label"])
            srow += 1

        wb.close()
        output.seek(0)

        response = HttpResponse(
            output,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f"attachment; filename={report_type}_{stamp}.xlsx"
        return response
