"""
Hisobotning Excel (xlsx) eksporti — "dashboard" uslubida:

  - "Umumiy" varag'i: KPI kartochkalar + asosiy diagrammalar (filiallar,
    kategoriyalar, to'lovlar) — rahbar bir qarashda ko'radigan sahifa.
  - Har bo'lim alohida varaqda: rangli sarlavha, zebra-qatorli jadval,
    autofilter, muzlatilgan sarlavha qatori, JAMI qatori, data-bar
    ko'rsatkichlari va tegishli diagramma (ustun/halqa/gorizontal bar).

Ranglar dataviz palitrasidan (oq fonda CVD-validatsiyadan o'tgan):
har bir diagramma yonida to'liq jadval bo'lgani uchun kontrast WARN'lari
(aqua/sariq/pushti) qoplangan. Servis DB'ga tegmaydi — faqat oldindan
aggregatsiya qilingan `data` dict'ini xlsx'ga chizadi.
"""
import io
from decimal import Decimal, InvalidOperation

import xlsxwriter

# ─── Dataviz palitra (oq fonda validatsiyadan o'tgan kategorik slotlar) ───
SERIES = [
    "#2A78D6",  # 1 ko'k
    "#EB6834",  # 2 to'q sariq
    "#1BAF7A",  # 3 akva
    "#EDA100",  # 4 sariq
    "#E87BA4",  # 5 pushti
    "#008300",  # 6 yashil
    "#4A3AA7",  # 7 binafsha
    "#E34948",  # 8 qizil
]
OTHER_GRAY = "#898781"   # "Boshqalar" bo'lagi — neytral kulrang
INK        = "#0B0B0B"   # asosiy matn
SECONDARY  = "#52514E"   # ikkinchi darajali matn
GRID       = "#E1E0D9"   # chiziqlar
ZEBRA_BG   = "#F5F7FA"   # juft qatorlar foni
HEADER_BG  = "#184F95"   # jadval sarlavhasi (ko'k ramp 600)
TITLE_BG   = "#0D366B"   # sahifa sarlavha bandi (ko'k ramp 700)
GOOD_TEXT  = "#006300"   # musbat (foyda)
BAD_RED    = "#D03B3B"   # chiqim/qarz urg'usi

# KPI kartochkalari: (bg tint, aksent chiziq rangi)
KPI_TINTS = {
    "blue":   ("#E3EEFB", "#2A78D6"),
    "green":  ("#E2F3E2", "#008300"),
    "red":    ("#FBE7E4", "#E34948"),
    "yellow": ("#FDF3DC", "#EDA100"),
    "violet": ("#EFEDF9", "#4A3AA7"),
    "pink":   ("#FCEBF2", "#E87BA4"),
}

MONEY_NUM = "#,##0"
PCT_NUM   = '0.0"%"'


def _num(value) -> float:
    """Decimal/None/str aralash qiymatlarni xavfsiz float'ga o'tkazadi."""
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("%", "").strip() or 0)
    except (ValueError, InvalidOperation):
        return 0.0


def _pct(value):
    """'12.3%' | 12.3 | Decimal → float yoki None (yozib bo'lmasa)."""
    if value is None or value == "":
        return None
    return _num(value)


class _ReportWorkbook:
    """Bitta hisobot workbook'ini quruvchi ichki yordamchi."""

    def __init__(self, workbook: xlsxwriter.Workbook, meta: dict):
        self.wb = workbook
        self.meta = meta or {}
        self._build_formats()

    # ─────────────────── formatlar ───────────────────
    def _fmt(self, **props):
        base = {"font_name": "Calibri", "font_size": 10, "font_color": INK}
        base.update(props)
        return self.wb.add_format(base)

    def _build_formats(self):
        f = self._fmt
        self.f_title = f(bold=True, font_size=15, font_color="#FFFFFF",
                         bg_color=TITLE_BG, align="left", valign="vcenter", indent=1)
        self.f_meta = f(font_size=9, font_color=SECONDARY, italic=True,
                        align="left", valign="vcenter", indent=1)
        self.f_header = f(bold=True, font_size=10, font_color="#FFFFFF",
                          bg_color=HEADER_BG, align="center", valign="vcenter",
                          border=1, border_color=HEADER_BG, text_wrap=True)
        self.f_section = f(bold=True, font_size=11, font_color=TITLE_BG)

        # Jadval katakchalari: (oddiy, zebra) juftliklari har tur uchun
        def cell_pair(**extra):
            plain = f(border=1, border_color=GRID, **extra)
            zebra = f(border=1, border_color=GRID, bg_color=ZEBRA_BG, **extra)
            return plain, zebra

        self.c_text  = cell_pair(align="left",  valign="vcenter", indent=1)
        self.c_money = cell_pair(align="right", valign="vcenter", num_format=MONEY_NUM)
        self.c_int   = cell_pair(align="center", valign="vcenter", num_format="#,##0")
        self.c_pct   = cell_pair(align="center", valign="vcenter", num_format=PCT_NUM)

        self.f_total_label = f(bold=True, bg_color="#E9EEF6", border=1,
                               border_color=GRID, align="left", indent=1, top=2, top_color=HEADER_BG)
        self.f_total_money = f(bold=True, bg_color="#E9EEF6", border=1, border_color=GRID,
                               align="right", num_format=MONEY_NUM, top=2, top_color=HEADER_BG)
        self.f_total_int = f(bold=True, bg_color="#E9EEF6", border=1, border_color=GRID,
                             align="center", num_format="#,##0", top=2, top_color=HEADER_BG)
        self.f_total_blank = f(bg_color="#E9EEF6", border=1, border_color=GRID,
                               top=2, top_color=HEADER_BG)
        self.f_note = f(font_size=9, font_color=SECONDARY, italic=True)

    # ─────────────────── umumiy bloklar ───────────────────
    def sheet(self, name: str, tab_color: str):
        ws = self.wb.add_worksheet(name)
        ws.set_tab_color(tab_color)
        ws.hide_gridlines(2)
        return ws

    def title_band(self, ws, title: str, last_col: int):
        """0-qator: rangli sarlavha bandi, 1-qator: davr/do'kon/sana meta."""
        ws.set_row(0, 30)
        ws.merge_range(0, 0, 0, last_col, title, self.f_title)
        meta_bits = []
        if self.meta.get("period"):
            meta_bits.append(f"Davr: {self.meta['period']}")
        if self.meta.get("store"):
            meta_bits.append(f"Do'kon: {self.meta['store']}")
        if self.meta.get("generated"):
            meta_bits.append(f"Yaratildi: {self.meta['generated']}")
        ws.merge_range(1, 0, 1, last_col, "  |  ".join(meta_bits), self.f_meta)

    def table(self, ws, start_row: int, columns: list[dict], rows: list[list], totals: bool = True):
        """
        Rangli jadval: sarlavha + zebra qatorlar + (ixtiyoriy) JAMI qatori.
        columns: {header, width, kind: text|money|int|pct, total: sum|count|None}
        Qaytaradi: (header_row, first_data, last_data)
        """
        kinds = {"text": self.c_text, "money": self.c_money,
                 "int": self.c_int, "pct": self.c_pct}
        header_row = start_row
        first_data = start_row + 1

        for col, spec in enumerate(columns):
            ws.set_column(col, col, spec.get("width", 14))
            ws.write(header_row, col, spec["header"], self.f_header)
        ws.set_row(header_row, 22)

        for i, row_vals in enumerate(rows):
            r = first_data + i
            pair_idx = i % 2  # zebra
            for col, value in enumerate(row_vals):
                kind = columns[col].get("kind", "text")
                fmt = kinds[kind][pair_idx]
                if kind == "text":
                    ws.write_string(r, col, "-" if value in (None, "") else str(value), fmt)
                elif kind == "pct":
                    p = _pct(value)
                    if p is None:
                        ws.write_string(r, col, "-", fmt)
                    else:
                        ws.write_number(r, col, p, fmt)
                else:
                    ws.write_number(r, col, _num(value), fmt)

        last_data = first_data + len(rows) - 1 if rows else first_data - 1

        if totals and rows:
            tr = last_data + 1
            for col, spec in enumerate(columns):
                mode = spec.get("total")
                if col == 0:
                    ws.write_string(tr, 0, "JAMI", self.f_total_label)
                elif mode == "sum":
                    col_l = xlsxwriter.utility.xl_col_to_name(col)
                    fmt = self.f_total_money if spec.get("kind") == "money" else self.f_total_int
                    ws.write_formula(
                        tr, col,
                        f"=SUM({col_l}{first_data + 1}:{col_l}{last_data + 1})", fmt,
                    )
                else:
                    ws.write_blank(tr, col, None, self.f_total_blank)

        if rows:
            ws.autofilter(header_row, 0, last_data, len(columns) - 1)
        ws.freeze_panes(first_data, 0)
        return header_row, first_data, last_data

    def data_bars(self, ws, first_data: int, last_data: int, col: int, color: str = SERIES[0]):
        if last_data < first_data:
            return
        ws.conditional_format(first_data, col, last_data, col, {
            "type": "data_bar",
            "bar_color": color,
            "bar_solid": False,
            "bar_border_color": color,
            "bar_direction": "left",
        })

    def empty_note(self, ws, row: int):
        ws.write_string(row, 0, "Bu davr uchun ma'lumot yo'q.", self.f_note)

    # ─────────────────── diagrammalar ───────────────────
    def _chart_base(self, chart, title: str):
        chart.set_title({"name": title,
                         "name_font": {"name": "Calibri", "size": 11, "bold": True, "color": INK}})
        chart.set_chartarea({"border": {"color": GRID}})
        chart.set_plotarea({"border": {"none": True}})
        return chart

    def doughnut(self, ws, sheet_name: str, labels_ref, values_ref, n: int,
                 title: str, insert_at: str, colors: list[str] | None = None,
                 width: int = 460, height: int = 300):
        """labels_ref/values_ref: (first_row, col, last_row, col) — shu varaqdagi manba."""
        if n <= 0:
            return
        chart = self.wb.add_chart({"type": "doughnut"})
        palette = colors or SERIES
        points = [{"fill": {"color": palette[i % len(palette)]},
                   "border": {"color": "#FFFFFF"}} for i in range(n)]
        chart.add_series({
            "name": title,
            "categories": [sheet_name, labels_ref[0], labels_ref[1], labels_ref[2], labels_ref[3]],
            "values":     [sheet_name, values_ref[0], values_ref[1], values_ref[2], values_ref[3]],
            "points": points,
            "data_labels": {"percentage": True,
                            "font": {"name": "Calibri", "size": 9, "color": "#FFFFFF", "bold": True}},
        })
        chart.set_hole_size(55)
        chart.set_legend({"position": "right", "font": {"name": "Calibri", "size": 9}})
        self._chart_base(chart, title)
        chart.show_hidden_data()
        chart.set_size({"width": width, "height": height})
        # object_position=2 — yashirin manba ustunlari diagramma o'lchamini buzmasin
        ws.insert_chart(insert_at, chart, {"x_offset": 4, "y_offset": 4, "object_position": 2})

    def column_chart(self, ws, sheet_name: str, labels_ref, values_ref, n: int,
                     title: str, insert_at: str, color: str = SERIES[0],
                     width: int = 560, height: int = 300):
        if n <= 0:
            return
        chart = self.wb.add_chart({"type": "column"})
        series = {
            "name": title,
            "categories": [sheet_name, labels_ref[0], labels_ref[1], labels_ref[2], labels_ref[3]],
            "values":     [sheet_name, values_ref[0], values_ref[1], values_ref[2], values_ref[3]],
            "fill": {"color": color},
            "gap": 60,
        }
        if n <= 8:  # kam ustunda qiymatni ustida ko'rsatamiz
            series["data_labels"] = {"value": True, "num_format": MONEY_NUM,
                                     "font": {"name": "Calibri", "size": 8, "color": SECONDARY}}
        chart.add_series(series)
        chart.set_legend({"none": True})
        chart.set_x_axis({"num_font": {"name": "Calibri", "size": 9, "color": SECONDARY},
                          "line": {"color": GRID}})
        chart.set_y_axis({"num_format": MONEY_NUM,
                          "num_font": {"name": "Calibri", "size": 9, "color": SECONDARY},
                          "major_gridlines": {"visible": True, "line": {"color": GRID}},
                          "line": {"none": True}})
        self._chart_base(chart, title)
        chart.show_hidden_data()
        chart.set_size({"width": width, "height": height})
        ws.insert_chart(insert_at, chart, {"x_offset": 4, "y_offset": 4, "object_position": 2})

    def bar_chart(self, ws, sheet_name: str, labels_ref, values_ref, n: int,
                  title: str, insert_at: str, color: str = SERIES[0],
                  width: int = 620, height: int = 340):
        if n <= 0:
            return
        chart = self.wb.add_chart({"type": "bar"})
        chart.add_series({
            "name": title,
            "categories": [sheet_name, labels_ref[0], labels_ref[1], labels_ref[2], labels_ref[3]],
            "values":     [sheet_name, values_ref[0], values_ref[1], values_ref[2], values_ref[3]],
            "fill": {"color": color},
            "gap": 40,
            "data_labels": {"value": True, "num_format": MONEY_NUM,
                            "font": {"name": "Calibri", "size": 8, "color": SECONDARY}},
        })
        chart.set_legend({"none": True})
        # Bar chartda o'qlar almashadi: x_axis — vertikal kategoriya o'qi,
        # y_axis — gorizontal qiymat o'qi. reverse — eng kattasi tepada tursin.
        chart.set_x_axis({"reverse": True,
                          "num_font": {"name": "Calibri", "size": 9, "color": SECONDARY},
                          "line": {"color": GRID}})
        chart.set_y_axis({"num_format": MONEY_NUM,
                          "num_font": {"name": "Calibri", "size": 9, "color": SECONDARY},
                          "major_gridlines": {"visible": True, "line": {"color": GRID}},
                          "line": {"none": True}})
        self._chart_base(chart, title)
        chart.show_hidden_data()
        chart.set_size({"width": width, "height": height})
        ws.insert_chart(insert_at, chart, {"x_offset": 4, "y_offset": 4, "object_position": 2})

    def chart_source(self, ws, start_col: int, pairs: list[tuple[str, float]]):
        """
        Diagramma manbasi uchun yashirin ustunlarga (label, value) yozadi.
        Qaytaradi: (labels_ref, values_ref, n) — doughnut/column'ga tayyor.
        """
        for i, (label, value) in enumerate(pairs):
            ws.write_string(i, start_col, str(label))
            ws.write_number(i, start_col + 1, _num(value))
        ws.set_column(start_col, start_col + 1, None, None, {"hidden": True})
        n = len(pairs)
        return (0, start_col, n - 1, start_col), (0, start_col + 1, n - 1, start_col + 1), n

    # ─────────────────── KPI kartochka ───────────────────
    def kpi_tile(self, ws, row: int, col: int, span: int, label: str, value,
                 tint: str, is_money: bool = True, value_color: str = INK):
        bg, accent = KPI_TINTS[tint]
        f_label = self._fmt(font_size=9, bold=True, font_color=SECONDARY, bg_color=bg,
                            align="left", valign="bottom", indent=1,
                            top=5, top_color=accent, left=1, left_color=GRID, right=1, right_color=GRID)
        num_format = MONEY_NUM if is_money else "#,##0"
        f_value = self._fmt(font_size=16, bold=True, font_color=value_color, bg_color=bg,
                            align="left", valign="top", indent=1, num_format=num_format,
                            bottom=1, bottom_color=GRID, left=1, left_color=GRID, right=1, right_color=GRID)
        ws.merge_range(row, col, row, col + span - 1, label.upper(), f_label)
        ws.merge_range(row + 1, col, row + 1, col + span - 1, _num(value), f_value)


class ExcelExportService:

    @staticmethod
    def generate_report(data: dict, meta: dict | None = None):
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        b = _ReportWorkbook(workbook, meta or {})

        # Varaqlar tartibi (yaratilish tartibida ko'rinadi)
        ws_main  = b.sheet("Umumiy",              TITLE_BG)
        ws_br    = b.sheet("Filiallar",           SERIES[0])
        ws_cat   = b.sheet("Kategoriyalar",       SERIES[2])
        ws_top   = b.sheet("Top mahsulotlar",     SERIES[6])
        ws_pay   = b.sheet("To'lovlar",           SERIES[3])
        ws_card  = b.sheet("Kartalar kesimi",     SERIES[4])
        ws_exp   = b.sheet("Xarajatlar",          SERIES[1])
        ws_cdebt = b.sheet("Mijoz qarzlari",      SERIES[7])
        ws_sdebt = b.sheet("Taminotchi qarzlari", BAD_RED)

        # ======================= 2. FILIALLAR =======================
        branches = data.get("branchStatistics") or []
        b.title_band(ws_br, "Filiallar kesimi", 4)
        cols = [
            {"header": "Filial",      "width": 28, "kind": "text"},
            {"header": "Daromad",     "width": 16, "kind": "money", "total": "sum"},
            {"header": "Buyurtmalar", "width": 13, "kind": "int",   "total": "sum"},
            {"header": "Mijozlar",    "width": 12, "kind": "int",   "total": "sum"},
        ]
        rows = [[r.get("store__name") or "-", r.get("revenue"), r.get("orders"), r.get("customers")]
                for r in branches]
        _, fd, ld = b.table(ws_br, 3, cols, rows)
        if rows:
            b.data_bars(ws_br, fd, ld, 1)
            b.column_chart(
                ws_br, "Filiallar",
                (fd, 0, ld, 0), (fd, 1, ld, 1), len(rows),
                "Filiallar bo'yicha daromad", "F4",
            )
        else:
            b.empty_note(ws_br, 4)

        # ======================= 3. KATEGORIYALAR =======================
        categories = data.get("categoryStatistics") or []
        b.title_band(ws_cat, "Kategoriyalar bo'yicha sotuv", 3)
        cols = [
            {"header": "Kategoriya", "width": 30, "kind": "text"},
            {"header": "Daromad",    "width": 16, "kind": "money", "total": "sum"},
            {"header": "Ulushi",     "width": 10, "kind": "pct"},
        ]
        rows = [[c.get("categoryName") or "Kategoriyasiz", c.get("revenue"), c.get("percent")]
                for c in categories]
        _, fd, ld = b.table(ws_cat, 3, cols, rows)
        cat_pairs = []
        if rows:
            b.data_bars(ws_cat, fd, ld, 1, SERIES[2])
            # Diagramma: top-7 + Boshqalar (yashirin manba ustunlari orqali)
            cat_pairs = [(r[0], _num(r[1])) for r in rows[:7]]
            rest = sum(_num(r[1]) for r in rows[7:])
            if rest > 0:
                cat_pairs.append(("Boshqalar", rest))
            colors = SERIES[:len(cat_pairs)]
            if rest > 0:
                colors = SERIES[:len(cat_pairs) - 1] + [OTHER_GRAY]
            l_ref, v_ref, n = b.chart_source(ws_cat, 8, cat_pairs)
            b.doughnut(ws_cat, "Kategoriyalar", l_ref, v_ref, n,
                       "Kategoriyalar ulushi", "E4", colors)
        else:
            b.empty_note(ws_cat, 4)

        # ======================= 4. TOP MAHSULOTLAR =======================
        top = data.get("topSellingProducts") or []
        b.title_band(ws_top, "Eng ko'p sotilgan mahsulotlar", 4)
        cols = [
            {"header": "#",          "width": 5,  "kind": "int"},
            {"header": "Mahsulot",   "width": 42, "kind": "text"},
            {"header": "Kategoriya", "width": 20, "kind": "text"},
            {"header": "Sotilgan",   "width": 11, "kind": "int",   "total": "sum"},
            {"header": "Daromad",    "width": 16, "kind": "money", "total": "sum"},
        ]
        rows = [[p.get("rank"), p.get("name") or "-", p.get("category") or "-",
                 p.get("totalSold"), p.get("totalRevenue")] for p in top]
        # Birinchi ustun "#" — JAMI yorlig'i shu ustunga tushmasin deb text sifatida yozamiz
        _, fd, ld = b.table(ws_top, 3, cols, rows)
        if rows:
            b.data_bars(ws_top, fd, ld, 4, SERIES[6])
            chart_pairs = [(r[1], _num(r[4])) for r in
                           sorted(rows, key=lambda x: _num(x[4]), reverse=True)[:10]]
            l_ref, v_ref, n = b.chart_source(ws_top, 9, chart_pairs)
            b.bar_chart(ws_top, "Top mahsulotlar", l_ref, v_ref, n,
                        "TOP-10 mahsulot (daromad)", "G4", SERIES[6])
        else:
            b.empty_note(ws_top, 4)

        # ======================= 5. TO'LOVLAR =======================
        payments = data.get("paymentStructure") or []
        b.title_band(ws_pay, "To'lov usullari", 3)
        cols = [
            {"header": "To'lov usuli",  "width": 22, "kind": "text"},
            {"header": "Sotuvlar soni", "width": 14, "kind": "int",   "total": "sum"},
            {"header": "Summa",         "width": 16, "kind": "money", "total": "sum"},
            {"header": "Ulushi",        "width": 10, "kind": "pct"},
        ]
        rows = [[p.get("method") or p.get("type") or "-", p.get("count"),
                 p.get("amount"), p.get("percent")] for p in payments]
        _, fd, ld = b.table(ws_pay, 3, cols, rows)
        pay_pairs = [(r[0], _num(r[2])) for r in rows if _num(r[2]) > 0]
        if rows:
            b.data_bars(ws_pay, fd, ld, 2, SERIES[3])
            if pay_pairs:
                l_ref, v_ref, n = b.chart_source(ws_pay, 8, pay_pairs)
                b.doughnut(ws_pay, "To'lovlar", l_ref, v_ref, n,
                           "To'lov usullari ulushi", "F4")
        else:
            b.empty_note(ws_pay, 4)

        # ======================= 6. KARTALAR KESIMI =======================
        cards = data.get("cardBreakdown") or []
        b.title_band(ws_card, "Bank kartalari kesimi", 3)
        cols = [
            {"header": "Karta",         "width": 22, "kind": "text"},
            {"header": "Sotuvlar soni", "width": 14, "kind": "int",   "total": "sum"},
            {"header": "Summa",         "width": 16, "kind": "money", "total": "sum"},
            {"header": "Ulushi",        "width": 10, "kind": "pct"},
        ]
        rows = [[c.get("name") or "Noma'lum karta", c.get("count"),
                 c.get("amount"), c.get("percent")] for c in cards]
        _, fd, ld = b.table(ws_card, 3, cols, rows)
        if rows:
            b.data_bars(ws_card, fd, ld, 2, SERIES[4])
            pairs = [(r[0], _num(r[2])) for r in rows if _num(r[2]) > 0][:7]
            rest = sum(_num(r[2]) for r in rows if _num(r[2]) > 0) - sum(v for _, v in pairs)
            colors = SERIES[:len(pairs)]
            if rest > 0:
                pairs.append(("Boshqalar", rest))
                colors = colors + [OTHER_GRAY]
            if pairs:
                l_ref, v_ref, n = b.chart_source(ws_card, 8, pairs)
                b.doughnut(ws_card, "Kartalar kesimi", l_ref, v_ref, n,
                           "Kartalar bo'yicha tushum", "F4", colors)
        else:
            b.empty_note(ws_card, 4)

        # ======================= 7. XARAJATLAR (yangi) =======================
        expenses = data.get("expenses") or []
        b.title_band(ws_exp, "Davr xarajatlari (chiqimlar)", 3)
        cols = [
            {"header": "Chiqim turi", "width": 38, "kind": "text"},
            {"header": "Soni",        "width": 10, "kind": "int",   "total": "sum"},
            {"header": "Summa",       "width": 16, "kind": "money", "total": "sum"},
            {"header": "Ulushi",      "width": 10, "kind": "pct"},
        ]
        rows = [[e.get("method") or "-", e.get("count"), e.get("amount"), e.get("percent")]
                for e in expenses]
        _, fd, ld = b.table(ws_exp, 3, cols, rows)
        if rows:
            b.data_bars(ws_exp, fd, ld, 2, SERIES[1])
            pairs = [(r[0], _num(r[2])) for r in rows[:10] if _num(r[2]) > 0]
            l_ref, v_ref, n = b.chart_source(ws_exp, 8, pairs)
            b.bar_chart(ws_exp, "Xarajatlar", l_ref, v_ref, n,
                        "Yirik chiqimlar", "F4", SERIES[1])
        else:
            b.empty_note(ws_exp, 4)

        # ======================= 8. MIJOZ QARZLARI =======================
        cdebts = (data.get("debts") or {}).get("customerDebts") or []
        b.title_band(ws_cdebt, "Mijoz qarzlari", 2)
        cols = [
            {"header": "Mijoz",   "width": 32, "kind": "text"},
            {"header": "Telefon", "width": 18, "kind": "text"},
            {"header": "Qarz",    "width": 16, "kind": "money", "total": "sum"},
        ]
        rows = [[d.get("customerName") or "-", d.get("phone") or "-", d.get("debt")]
                for d in cdebts]
        _, fd, ld = b.table(ws_cdebt, 3, cols, rows)
        if rows:
            b.data_bars(ws_cdebt, fd, ld, 2, BAD_RED)
        else:
            b.empty_note(ws_cdebt, 4)

        # ======================= 9. TA'MINOTCHI QARZLARI =======================
        sdebts = (data.get("debts") or {}).get("supplierDebts") or []
        b.title_band(ws_sdebt, "Ta'minotchi qarzlari", 1)
        cols = [
            {"header": "Ta'minotchi", "width": 32, "kind": "text"},
            {"header": "Qarz",        "width": 16, "kind": "money", "total": "sum"},
        ]
        rows = [[d.get("supplierName") or "-", d.get("debt")] for d in sdebts]
        _, fd, ld = b.table(ws_sdebt, 3, cols, rows)
        if rows:
            b.data_bars(ws_sdebt, fd, ld, 1, BAD_RED)
        else:
            b.empty_note(ws_sdebt, 4)

        # ======================= 1. UMUMIY (dashboard) =======================
        summary = data.get("summary") or {}
        # Ustun kengliklari: A tor, keyin 3 tadan blok (gap bilan)
        ws_main.set_column(0, 0, 2)
        for c in (1, 2, 3, 5, 6, 7, 9, 10, 11):
            ws_main.set_column(c, c, 11)
        for c in (4, 8):
            ws_main.set_column(c, c, 2)

        b.title_band(ws_main, "AUTOCRM — UMUMIY HISOBOT", 12)
        ws_main.set_row(2, 8)

        tiles = [
            ("Jami daromad",     summary.get("totalRevenue"),      "blue",   True,  INK),
            ("Sof foyda",        summary.get("totalProfit"),       "green",  True,  GOOD_TEXT),
            ("Xarajat",          summary.get("totalExpenses"),     "red",    True,  BAD_RED),
            ("Buyurtmalar soni", summary.get("totalOrders"),       "yellow", False, INK),
            ("O'rtacha chek",    summary.get("averageOrderValue"), "violet", True,  INK),
            ("Mijozlar soni",    summary.get("totalCustomers"),    "pink",   False, INK),
        ]
        positions = [(3, 1), (3, 5), (3, 9), (6, 1), (6, 5), (6, 9)]
        for (label, value, tint, is_money, vcolor), (r, c) in zip(tiles, positions):
            ws_main.set_row(r, 16)
            ws_main.set_row(r + 1, 26)
            b.kpi_tile(ws_main, r, c, 3, label, value, tint, is_money, vcolor)

        ws_main.set_row(8, 10)

        # Dashboard diagrammalari — manba boshqa varaqlardagi jadvallar
        chart_row = 9
        if branches:
            n = len(branches)
            b.column_chart(ws_main, "Filiallar",
                           (4, 0, 3 + n, 0), (4, 1, 3 + n, 1), n,
                           "Filiallar bo'yicha daromad", f"B{chart_row + 1}",
                           SERIES[0], 440, 290)
        if cat_pairs:
            n = len(cat_pairs)
            colors = SERIES[:n]
            if cat_pairs[-1][0] == "Boshqalar":
                colors = SERIES[:n - 1] + [OTHER_GRAY]
            b.doughnut(ws_main, "Kategoriyalar",
                       (0, 8, n - 1, 8), (0, 9, n - 1, 9), n,
                       "Kategoriyalar ulushi", f"H{chart_row + 1}", colors, 440, 290)
        if pay_pairs:
            n = len(pay_pairs)
            b.doughnut(ws_main, "To'lovlar",
                       (0, 8, n - 1, 8), (0, 9, n - 1, 9), n,
                       "To'lov usullari", f"B{chart_row + 17}", SERIES, 440, 290)

        ws_main.activate()
        workbook.close()
        output.seek(0)
        return output
