"""
Universal PDF hisobot generatori (Reports moduli).

Mavjud ReportLab kutubxonasidan foydalangan holda hisobotlarni
chiroyli A4 landscape formatidagi PDF hujjatiga aylantiradi.
"""
from __future__ import annotations

from datetime import datetime
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


class ReportPdfService:

    @staticmethod
    def render(
        label: str,
        columns: list[dict],
        rows: list[dict],
        summary: list[dict],
        info: dict | None = None,
        params: dict | None = None,
    ) -> bytes:
        params = params or {}
        report_type = params.get("report_type", "")
        metric = (params.get("metric") or "revenue").strip().lower()

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=landscape(A4),
            leftMargin=20,
            rightMargin=20,
            topMargin=20,
            bottomMargin=20,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "ReportTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            textColor=colors.HexColor("#0D366B"),
        )
        meta_style = ParagraphStyle(
            "ReportMeta",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#475569"),
        )
        cell_style = ParagraphStyle(
            "ReportCell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
        )
        hdr_style = ParagraphStyle(
            "ReportHdr",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.white,
            alignment=1,
        )

        story = []

        # 1. Sarlavha
        story.append(Paragraph(str(label), title_style))
        story.append(Spacer(1, 4))

        # 2. Metama'lumotlar / Scope
        date_from = params.get("from") or params.get("date_from") or ""
        date_to = params.get("to") or params.get("date_to") or ""
        date_str = f"{date_from} — {date_to}" if date_from and date_to else "Barcha davr"

        meta_parts = [f"<b>Davr:</b> {date_str}"]

        if report_type == "abc_analysis":
            metric_labels = {
                "revenue": "Sof tushum (Net revenue)",
                "profit": "Sof foyda (Net profit)",
                "quantity": "Sof sotilgan miqdor (Net sold qty)",
            }
            meta_parts.append(f"<b>Tanlangan metrika:</b> {metric_labels.get(metric, 'Sof tushum')}")

        abc_class_filter = params.get("abc_class")
        if abc_class_filter:
            meta_parts.append(f"<b>ABC toifa filtri:</b> {abc_class_filter.upper()}")

        if report_type == "inventory_results":
            session_val = params.get("session_id")
            if session_val:
                meta_parts.append(f"<b>Sessiya ID:</b> {session_val}")
            st_filter = params.get("status")
            if st_filter and st_filter != "all":
                meta_parts.append(f"<b>Holat filtri:</b> {st_filter}")

        if report_type == "order_returns":
            ret_id = params.get("return_id")
            if ret_id:
                meta_parts.append(f"<b>Qaytarish №:</b> {ret_id}")
            ord_id = params.get("order_id")
            if ord_id:
                meta_parts.append(f"<b>Buyurtma №:</b> {ord_id}")

        if report_type == "write_offs":
            wo_id = params.get("write_off_id")
            if wo_id:
                meta_parts.append(f"<b>Hujjat №:</b> {wo_id}")
            rsn = params.get("reason")
            if rsn:
                meta_parts.append(f"<b>Sabab:</b> {rsn}")

        if report_type == "imports":
            ent_id = params.get("entry_id")
            if ent_id:
                meta_parts.append(f"<b>Hujjat №:</b> {ent_id}")
            p_status = params.get("payment_status")
            if p_status:
                meta_parts.append(f"<b>To'lov holati:</b> {p_status}")

        store_val = params.get("store_id")
        if store_val and store_val != "all":
            meta_parts.append(f"<b>Do'kon ID:</b> {store_val}")
        else:
            meta_parts.append("<b>Do'kon:</b> Barcha do'konlar")

        gen_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        meta_parts.append(f"<b>Yaratildi:</b> {gen_time} | <b>Jami qatorlar:</b> {len(rows)}")

        story.append(Paragraph(" &nbsp;&nbsp;|&nbsp;&nbsp; ".join(meta_parts), meta_style))
        story.append(Spacer(1, 10))

        # 3. Summary kartochkalari
        if summary:
            summary_cells = []
            for s in summary:
                lbl = s.get("label", "")
                val = s.get("value", "")
                hint = s.get("hint")
                hint_str = f" <font color='#64748B' size='7'>({hint})</font>" if hint else ""
                summary_cells.append(Paragraph(f"<b>{lbl}:</b> {val}{hint_str}", meta_style))

            # 4 ustunlik qilib joylashtirish
            chunk_size = 4
            summary_table_data = [
                summary_cells[i:i + chunk_size]
                for i in range(0, len(summary_cells), chunk_size)
            ]
            # Oxirgi qatordagi bo'sh kataklarni to'ldirish
            if summary_table_data:
                while len(summary_table_data[-1]) < chunk_size:
                    summary_table_data[-1].append(Paragraph("", meta_style))

                col_w = 800 / chunk_size
                st = Table(summary_table_data, colWidths=[col_w] * chunk_size)
                st.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]))
                story.append(st)
                story.append(Spacer(1, 10))

        # 4. Jadval (Table)
        if report_type == "abc_analysis":
            headers = [
                "#", "Tovar nomi", "SKU", "Toifa", "Metrika",
                "Ulush %", "Kumulyativ %", "Sof miqdor", "Sof tushum", "Sof foyda"
            ]
            col_widths = [25, 200, 75, 50, 85, 65, 75, 70, 80, 75]

            table_data = [[Paragraph(f"<b>{h}</b>", hdr_style) for h in headers]]
            for idx, r in enumerate(rows[:500], start=1):
                cls_val = r.get("abc_class", "C")
                cls_color = "#166534" if cls_val == "A" else ("#1E40AF" if cls_val == "B" else "#B45309")
                cls_cell = Paragraph(f"<font color='{cls_color}'><b>{cls_val}</b></font>", cell_style)

                table_data.append([
                    Paragraph(str(idx), cell_style),
                    Paragraph(str(r.get("name") or "-"), cell_style),
                    Paragraph(str(r.get("sku") or "-"), cell_style),
                    cls_cell,
                    Paragraph(str(r.get("metric_value") or "0.00"), cell_style),
                    Paragraph(str(r.get("share_pct") or "0.00%"), cell_style),
                    Paragraph(str(r.get("cumulative_pct") or "0.00%"), cell_style),
                    Paragraph(str(r.get("net_sold_qty") or "0"), cell_style),
                    Paragraph(str(r.get("net_revenue") or "0.00"), cell_style),
                    Paragraph(str(r.get("profit") or "0.00"), cell_style),
                ])
        elif report_type == "inventory_results":
            headers = [
                "#", "Sessiya", "Do'kon", "Sana", "Tovar nomi", "SKU",
                "Kutilgan", "Sanalgan", "Tafovut", "Kamomad", "Ortiqcha",
                "Tannarx", "Kamomad sum", "Ortiqcha sum", "Yakuniy", "Holati"
            ]
            col_widths = [20, 35, 55, 55, 115, 45, 40, 40, 40, 45, 45, 50, 55, 55, 45, 65]

            table_data = [[Paragraph(f"<b>{h}</b>", hdr_style) for h in headers]]
            for idx, r in enumerate(rows[:500], start=1):
                st_val = r.get("raw_status") or r.get("status") or "unchecked"
                st_display = r.get("status") or st_val
                st_color = "#166534" if st_val == "matched" else (
                    "#DC2626" if st_val == "shortage" else (
                        "#2563EB" if st_val == "excess" else "#64748B"
                    )
                )
                st_cell = Paragraph(f"<font color='{st_color}'><b>{st_display}</b></font>", cell_style)

                table_data.append([
                    Paragraph(str(idx), cell_style),
                    Paragraph(str(r.get("session_id") or "-"), cell_style),
                    Paragraph(str(r.get("store_name") or "-"), cell_style),
                    Paragraph(str(r.get("session_date") or "-"), cell_style),
                    Paragraph(str(r.get("product_name") or "-"), cell_style),
                    Paragraph(str(r.get("sku") or "-"), cell_style),
                    Paragraph(str(r.get("expected_qty") if r.get("expected_qty") is not None else "-"), cell_style),
                    Paragraph(str(r.get("counted_qty") if r.get("counted_qty") is not None else "-"), cell_style),
                    Paragraph(str(r.get("difference_qty") if r.get("difference_qty") is not None else "-"), cell_style),
                    Paragraph(str(r.get("shortage_qty") if r.get("shortage_qty") is not None else "0"), cell_style),
                    Paragraph(str(r.get("excess_qty") if r.get("excess_qty") is not None else "0"), cell_style),
                    Paragraph(str(r.get("unit_cost") or "0.00"), cell_style),
                    Paragraph(str(r.get("shortage_value") or "0.00"), cell_style),
                    Paragraph(str(r.get("excess_value") or "0.00"), cell_style),
                    Paragraph(str(r.get("final_balance") if r.get("final_balance") is not None else "-"), cell_style),
                    st_cell,
                ])
        elif report_type == "order_returns":
            headers = [
                "#", "Qaytarish", "Chek", "Filial", "Sana", "Tovar", "SKU",
                "Miqdor", "Sotuv narx", "Tannarx", "Summa", "Tannarx qiymati", "Foyda ta'siri", "To'lov"
            ]
            col_widths = [20, 40, 35, 60, 60, 135, 50, 40, 60, 60, 65, 65, 60, 50]

            table_data = [[Paragraph(f"<b>{h}</b>", hdr_style) for h in headers]]
            for idx, r in enumerate(rows[:500], start=1):
                table_data.append([
                    Paragraph(str(idx), cell_style),
                    Paragraph(str(r.get("return_id") or "-"), cell_style),
                    Paragraph(str(r.get("order_id") or "-"), cell_style),
                    Paragraph(str(r.get("store_name") or "-"), cell_style),
                    Paragraph(str(r.get("return_datetime") or "-"), cell_style),
                    Paragraph(str(r.get("product_name") or "-"), cell_style),
                    Paragraph(str(r.get("sku") or "-"), cell_style),
                    Paragraph(str(r.get("returned_qty") or "0"), cell_style),
                    Paragraph(str(r.get("unit_sale_price") or "0.00"), cell_style),
                    Paragraph(str(r.get("unit_purchase_price") or "0.00"), cell_style),
                    Paragraph(str(r.get("refund_amount") or "0.00"), cell_style),
                    Paragraph(str(r.get("purchase_value") or "0.00"), cell_style),
                    Paragraph(str(r.get("profit_impact") or "0.00"), cell_style),
                    Paragraph(str(r.get("payment_method") or "-"), cell_style),
                ])
        elif report_type == "write_offs":
            headers = [
                "#", "Hujjat", "Filial", "Sana", "Sabab", "Xodim", "Tovar", "SKU",
                "Miqdor", "Tannarx", "Sotuv narx", "Tannarx qiymati", "Sotuv qiymati", "Foyda ta'siri"
            ]
            col_widths = [20, 45, 55, 55, 55, 55, 135, 50, 40, 55, 55, 60, 60, 60]

            table_data = [[Paragraph(f"<b>{h}</b>", hdr_style) for h in headers]]
            for idx, r in enumerate(rows[:500], start=1):
                table_data.append([
                    Paragraph(str(idx), cell_style),
                    Paragraph(str(r.get("write_off_id") or "-"), cell_style),
                    Paragraph(str(r.get("store_name") or "-"), cell_style),
                    Paragraph(str(r.get("write_off_datetime") or "-"), cell_style),
                    Paragraph(str(r.get("reason_display") or "-"), cell_style),
                    Paragraph(str(r.get("created_by_name") or "-"), cell_style),
                    Paragraph(str(r.get("product_name") or "-"), cell_style),
                    Paragraph(str(r.get("sku") or "-"), cell_style),
                    Paragraph(str(r.get("quantity") if r.get("quantity") is not None else "0"), cell_style),
                    Paragraph(str(r.get("unit_purchase_price") or "0.00"), cell_style),
                    Paragraph(str(r.get("unit_sale_price") or "0.00"), cell_style),
                    Paragraph(str(r.get("purchase_value") or "0.00"), cell_style),
                    Paragraph(str(r.get("sale_value") or "0.00"), cell_style),
                    Paragraph(str(r.get("profit_impact") or "0.00"), cell_style),
                ])
        elif report_type == "imports":
            headers = [
                "#", "Hujjat", "Filial", "Sana", "Ta'minotchi", "Tovar", "SKU",
                "Miqdor", "Tannarx", "Xarid sum", "Sotuv sum", "Qaytim", "Sof xarid", "To'lov"
            ]
            col_widths = [20, 40, 50, 55, 65, 125, 50, 45, 55, 65, 65, 45, 65, 55]

            table_data = [[Paragraph(f"<b>{h}</b>", hdr_style) for h in headers]]
            for idx, r in enumerate(rows[:500], start=1):
                st_val = r.get("raw_payment_status") or r.get("payment_status") or "unpaid"
                st_display = r.get("payment_status") or st_val
                st_color = "#166534" if st_val in ("paid", "To'langan") else (
                    "#B45309" if st_val in ("partial", "Qisman to'langan") else "#DC2626"
                )
                st_cell = Paragraph(f"<font color='{st_color}'><b>{st_display}</b></font>", cell_style)

                table_data.append([
                    Paragraph(str(idx), cell_style),
                    Paragraph(str(r.get("entry_id") or "-"), cell_style),
                    Paragraph(str(r.get("store_name") or "-"), cell_style),
                    Paragraph(str(r.get("entry_datetime") or "-"), cell_style),
                    Paragraph(str(r.get("supplier_name") or "-"), cell_style),
                    Paragraph(str(r.get("product_name") or "-"), cell_style),
                    Paragraph(str(r.get("sku") or "-"), cell_style),
                    Paragraph(str(r.get("quantity") if r.get("quantity") is not None else "0"), cell_style),
                    Paragraph(str(r.get("unit_purchase_price") or "0.00"), cell_style),
                    Paragraph(str(r.get("purchase_value") or "0.00"), cell_style),
                    Paragraph(str(r.get("sale_value") or "0.00"), cell_style),
                    Paragraph(str(r.get("returned_qty") or "0"), cell_style),
                    Paragraph(str(r.get("net_purchase_value") or "0.00"), cell_style),
                    st_cell,
                ])
        else:
            # Boshqa hisobotlar uchun dinamik ustunlar
            selected_cols = columns[:10]
            headers = [c["label"] for c in selected_cols]
            total_w = 800
            w_per_col = total_w / len(selected_cols) if selected_cols else 80
            col_widths = [w_per_col] * len(selected_cols)

            table_data = [[Paragraph(f"<b>{h}</b>", hdr_style) for h in headers]]
            for r in rows[:500]:
                row_cells = [Paragraph(str(r.get(c["key"], "-")), cell_style) for c in selected_cols]
                table_data.append(row_cells)

        dt = Table(table_data, colWidths=col_widths, repeatRows=1)
        dt_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#184F95")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        # Alternating rows
        for i in range(1, len(table_data)):
            if i % 2 == 0:
                dt_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F8FAFC")))
        dt.setStyle(TableStyle(dt_style))
        story.append(dt)

        if len(rows) > 500:
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"<i>* Eslatma: PDF formatida birinchi 500 ta qator ko'rsatildi (Jami: {len(rows)} qator). To'liq yuklab olish uchun Excel formatidan foydalaning.</i>", meta_style))

        doc.build(story)
        return buf.getvalue()
