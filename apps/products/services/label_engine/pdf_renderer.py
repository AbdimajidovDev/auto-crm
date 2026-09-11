import io
import os
from pathlib import Path
from typing import Any
from decimal import Decimal
from PIL import Image

from django.conf import settings
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .barcode_gen import BarcodeGeneratorService
from .resolver import LabelDataResolver
from .exceptions import LabelRenderingError


# Register fonts once
_FONTS_REGISTERED = False


def _register_custom_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return

    fonts_dir = Path(settings.BASE_DIR) / "assets" / "fonts"

    gilroy_bold = fonts_dir / "Gilroy-Bold.ttf"
    if gilroy_bold.is_file():
        try:
            pdfmetrics.registerFont(TTFont("Gilroy-Bold", str(gilroy_bold)))
        except Exception:
            pass

    gilroy_regular = fonts_dir / "Gilroy-Regular.ttf"
    if gilroy_regular.is_file():
        try:
            pdfmetrics.registerFont(TTFont("Gilroy-Regular", str(gilroy_regular)))
        except Exception:
            pass

    _FONTS_REGISTERED = True


def hex_to_rgb(hex_color: str | None) -> tuple[float, float, float] | None:
    if not hex_color or not isinstance(hex_color, str) or not hex_color.startswith("#"):
        return None
    hex_str = hex_color.lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(c * 2 for c in hex_str)
    if len(hex_str) != 6:
        return None
    try:
        r = int(hex_str[0:2], 16) / 255.0
        g = int(hex_str[2:4], 16) / 255.0
        b = int(hex_str[4:6], 16) / 255.0
        return (r, g, b)
    except ValueError:
        return None


class PdfLabelRenderer:
    """
    Renders high-precision vector PDF stream for thermal label printers using ReportLab.
    - True vector EAN-13 barcodes (no pixelation, maximum scanner readability)
    - Millimeter-accurate geometry with top-left canonical coordinate translation
    - Proper layer order based on z_index
    - Pagination for multi-copy label printing
    """

    MM_TO_PT = 2.83464567

    @classmethod
    def resolve_font_name(cls, font_family: str, bold: bool = False, italic: bool = False) -> str:
        _register_custom_fonts()
        font_family = font_family or "Helvetica"

        if font_family in ("Gilroy-Bold", "Gilroy-Regular"):
            try:
                # Check if registered in ReportLab
                pdfmetrics.getFont(font_family)
                return font_family
            except KeyError:
                # Fallback to Helvetica
                if "Bold" in font_family or bold:
                    return "Helvetica-Bold"
                return "Helvetica"

        if font_family == "Courier":
            if bold and italic:
                return "Courier-BoldOblique"
            if bold:
                return "Courier-Bold"
            if italic:
                return "Courier-Oblique"
            return "Courier"

        # Default Helvetica
        if bold and italic:
            return "Helvetica-BoldOblique"
        if bold:
            return "Helvetica-Bold"
        if italic:
            return "Helvetica-Oblique"
        return "Helvetica"

    @classmethod
    def truncate_text(cls, c: canvas.Canvas, text: str, font_name: str, font_size: float, max_width_pt: float) -> str:
        if c.stringWidth(text, font_name, font_size) <= max_width_pt:
            return text
        ellipsis = "..."
        for i in range(len(text), 0, -1):
            sub = text[:i] + ellipsis
            if c.stringWidth(sub, font_name, font_size) <= max_width_pt:
                return sub
        return ""

    @classmethod
    def render_pdf(
        cls,
        width_mm: float | Decimal,
        height_mm: float | Decimal,
        layout: dict[str, Any],
        items: list[tuple[dict[str, Any], int]],
    ) -> bytes:
        """
        Renders multi-copy vector PDF.
        items: list of (context_dict, quantity)
        Returns binary PDF bytes.
        """
        _register_custom_fonts()
        try:
            t_width_mm = float(width_mm)
            t_height_mm = float(height_mm)

            page_width_pt = t_width_mm * cls.MM_TO_PT
            page_height_pt = t_height_mm * cls.MM_TO_PT

            buffer = io.BytesIO()
            c = canvas.Canvas(buffer, pagesize=(page_width_pt, page_height_pt))

            bg_color_hex = layout.get("background_color", "#FFFFFF")
            bg_rgb = hex_to_rgb(bg_color_hex) or (1.0, 1.0, 1.0)

            elements = layout.get("elements", [])
            # Sort by z_index ascending
            sorted_elements = sorted(elements, key=lambda el: el.get("z_index", 0))

            for context, quantity in items:
                for _ in range(max(1, int(quantity))):
                    # 1. Draw page background
                    c.setFillColorRGB(*bg_rgb)
                    c.rect(0, 0, page_width_pt, page_height_pt, fill=1, stroke=0)

                    # 2. Draw elements
                    for elem in sorted_elements:
                        if not elem.get("visible", True):
                            continue
                        cls._draw_element(c, elem, context, t_height_mm)

                    c.showPage()

            c.save()
            buffer.seek(0)
            return buffer.getvalue()
        except Exception as e:
            raise LabelRenderingError(f"PDF generatsiya qilishda xatolik yuz berdi: {str(e)}") from e

    @classmethod
    def _draw_element(
        cls,
        c: canvas.Canvas,
        elem: dict[str, Any],
        context: dict[str, Any],
        template_height_mm: float,
    ):
        elem_type = elem.get("type")
        x_mm = float(elem.get("x_mm", 0))
        y_mm = float(elem.get("y_mm", 0))
        w_mm = float(elem.get("width_mm", 0))
        h_mm = float(elem.get("height_mm", 0))

        # Coordinate translation: top-left (x, y) to ReportLab bottom-left (rx, ry)
        rx = x_mm * cls.MM_TO_PT
        ry = (template_height_mm - y_mm - h_mm) * cls.MM_TO_PT
        rw = w_mm * cls.MM_TO_PT
        rh = h_mm * cls.MM_TO_PT

        if elem_type == "shape":
            cls._draw_shape(c, elem, rx, ry, rw, rh)
        elif elem_type == "line":
            cls._draw_line(c, elem, rx, ry, rw, rh)
        elif elem_type == "text":
            cls._draw_text(c, elem, context, rx, ry, rw, rh)
        elif elem_type == "barcode":
            cls._draw_barcode(c, elem, context, rx, ry, rw, rh)
        elif elem_type == "image":
            cls._draw_image(c, elem, context, rx, ry, rw, rh)

    @classmethod
    def _draw_shape(cls, c: canvas.Canvas, elem: dict[str, Any], rx: float, ry: float, rw: float, rh: float):
        bg_rgb = hex_to_rgb(elem.get("bg_color"))
        border_rgb = hex_to_rgb(elem.get("border_color"))
        border_w_pt = float(elem.get("border_width_mm", 0)) * cls.MM_TO_PT
        corner_r_pt = float(elem.get("corner_radius_mm", 0)) * cls.MM_TO_PT

        fill = 1 if bg_rgb else 0
        stroke = 1 if (border_rgb and border_w_pt > 0) else 0

        if fill:
            c.setFillColorRGB(*bg_rgb)
        if stroke:
            c.setStrokeColorRGB(*border_rgb)
            c.setLineWidth(border_w_pt)

        if fill or stroke:
            if corner_r_pt > 0:
                c.roundRect(rx, ry, rw, rh, corner_r_pt, fill=fill, stroke=stroke)
            else:
                c.rect(rx, ry, rw, rh, fill=fill, stroke=stroke)

    @classmethod
    def _draw_line(cls, c: canvas.Canvas, elem: dict[str, Any], rx: float, ry: float, rw: float, rh: float):
        color_rgb = hex_to_rgb(elem.get("color")) or (0.0, 0.0, 0.0)
        c.setFillColorRGB(*color_rgb)
        # Draw as solid rectangle
        c.rect(rx, ry, rw, rh, fill=1, stroke=0)

    @classmethod
    def _draw_text(
        cls,
        c: canvas.Canvas,
        elem: dict[str, Any],
        context: dict[str, Any],
        rx: float,
        ry: float,
        rw: float,
        rh: float,
    ):
        field = elem.get("field")
        text = str(context.get(field, "") if field else elem.get("static_value", ""))
        if not text:
            return

        # Optional element background
        elem_bg_rgb = hex_to_rgb(elem.get("bg_color"))
        if elem_bg_rgb:
            c.setFillColorRGB(*elem_bg_rgb)
            c.rect(rx, ry, rw, rh, fill=1, stroke=0)

        font_family = elem.get("font_family", "Helvetica")
        bold = bool(elem.get("bold", False))
        italic = bool(elem.get("italic", False))
        font_name = cls.resolve_font_name(font_family, bold, italic)
        font_size = float(elem.get("font_size", 8.0))

        color_rgb = hex_to_rgb(elem.get("color")) or (0.0, 0.0, 0.0)
        c.setFillColorRGB(*color_rgb)
        c.setFont(font_name, font_size)

        # Truncate text if wider than bounding box
        fitted_text = cls.truncate_text(c, text, font_name, font_size, rw)
        if not fitted_text:
            return

        # Vertical alignment: baseline positioned so text sits inside rh
        # In typography, cap height is ~0.7 * font_size, descent is ~0.2 * font_size
        text_baseline_y = ry + rh - (font_size * 0.85)

        align = elem.get("align", "left")
        if align == "center":
            c.drawCentredString(rx + (rw / 2.0), text_baseline_y, fitted_text)
        elif align == "right":
            c.drawRightString(rx + rw, text_baseline_y, fitted_text)
        else:
            c.drawString(rx, text_baseline_y, fitted_text)

        if elem.get("underline", False):
            tw = c.stringWidth(fitted_text, font_name, font_size)
            if align == "center":
                ux = rx + (rw - tw) / 2.0
            elif align == "right":
                ux = rx + rw - tw
            else:
                ux = rx
            c.setStrokeColorRGB(*color_rgb)
            c.setLineWidth(max(0.5, font_size * 0.06))
            c.line(ux, text_baseline_y - 1.0, ux + tw, text_baseline_y - 1.0)

    @classmethod
    def _draw_barcode(
        cls,
        c: canvas.Canvas,
        elem: dict[str, Any],
        context: dict[str, Any],
        rx: float,
        ry: float,
        rw: float,
        rh: float,
    ):
        raw_barcode = context.get("product.barcode") or "2000000000000"
        barcode_res = BarcodeGeneratorService.generate(str(raw_barcode))

        barcode_opts = elem.get("barcode_options") or {}
        show_text = bool(barcode_opts.get("show_text", True))
        font_size = float(barcode_opts.get("font_size", 5.5))

        if show_text and rh > (font_size + 2.0):
            text_area_h = font_size + 1.5
            bars_h = rh - text_area_h
            guard_drop = min(text_area_h * 0.45, 1.8)
        else:
            show_text = False
            bars_h = rh
            guard_drop = 0

        # Calculate module width (95 modules)
        total_modules = barcode_res.total_modules
        mod_w = rw / float(total_modules)

        # Draw vector barcode bars
        c.setFillColorRGB(0.0, 0.0, 0.0)
        top_y = ry + rh

        for idx, module in enumerate(barcode_res.modules):
            if module.is_black:
                mx = rx + (idx * mod_w)
                actual_h = bars_h + (guard_drop if module.is_guard else 0)
                my = top_y - actual_h
                # Draw sharp vector rectangle with slight overlap to prevent renderer anti-aliasing gaps
                c.rect(mx, my, mod_w + 0.02, actual_h, fill=1, stroke=0)

        # Draw digits text underneath
        if show_text:
            first_digit = barcode_res.barcode_value[0]
            left_part = barcode_res.barcode_value[1:7]
            right_part = barcode_res.barcode_value[7:13]

            c.setFont("Helvetica", font_size)
            text_baseline = ry + 0.5

            # Left 6 digits centered under modules 3..45
            left_center_x = rx + ((3 + 45) / 2.0 * mod_w)
            c.drawCentredString(left_center_x, text_baseline, left_part)

            # Right 6 digits centered under modules 50..92
            right_center_x = rx + ((50 + 92) / 2.0 * mod_w)
            c.drawCentredString(right_center_x, text_baseline, right_part)

            # First digit drawn at the left margin if space permits
            if rx >= (font_size * 0.6):
                c.drawRightString(rx - 0.5, text_baseline, first_digit)

    @classmethod
    def _draw_image(
        cls,
        c: canvas.Canvas,
        elem: dict[str, Any],
        context: dict[str, Any],
        rx: float,
        ry: float,
        rw: float,
        rh: float,
    ):
        source_type = elem.get("source_type", "field")
        if source_type == "uploaded":
            image_url = elem.get("image_url")
            file_path = LabelDataResolver.resolve_uploaded_image_path(image_url)
        else:
            field = elem.get("field")
            if not field:
                return
            file_path = context.get(field)

        if not file_path or not isinstance(file_path, str) or not os.path.isfile(file_path):
            return

        fit = elem.get("image_fit", "contain")

        try:
            with Image.open(file_path) as img:
                orig_w, orig_h = img.size

            if orig_w <= 0 or orig_h <= 0:
                return

            aspect = orig_w / orig_h
            box_aspect = rw / rh

            if fit == "contain":
                if aspect > box_aspect:
                    draw_w = rw
                    draw_h = rw / aspect
                else:
                    draw_h = rh
                    draw_w = rh * aspect
                draw_x = rx + (rw - draw_w) / 2.0
                draw_y = ry + (rh - draw_h) / 2.0
            else:
                # fill or cover
                draw_x, draw_y, draw_w, draw_h = rx, ry, rw, rh

            c.drawImage(file_path, draw_x, draw_y, draw_w, draw_h, mask="auto", preserveAspectRatio=False)
        except Exception:
            # Safe ignore failed image load to prevent print crash
            pass
