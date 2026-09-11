import io
import os
from pathlib import Path
from typing import Any
from decimal import Decimal
from PIL import Image, ImageDraw, ImageFont, ImageColor

from django.conf import settings
from .barcode_gen import BarcodeGeneratorService
from .resolver import LabelDataResolver
from .exceptions import LabelRenderingError


class PngLabelRenderer:
    """
    Renders single barcode label preview image (PNG) using Pillow.
    - Uses same canonical top-left coordinate system (x_mm, y_mm)
    - True 300 DPI high-resolution rendering
    - Z-index layer ordering
    - Bounding-box text truncation and alignment
    """

    DEFAULT_DPI = 300
    MM_PER_INCH = 25.4

    # System font fallbacks
    SYSTEM_SANS_BOLD = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    SYSTEM_SANS = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    SYSTEM_MONO = [
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]

    @classmethod
    def mm_to_px(cls, mm_val: float, dpi: int) -> int:
        return int(round(float(mm_val) * (dpi / cls.MM_PER_INCH)))

    @classmethod
    def pt_to_px(cls, pt_val: float, dpi: int) -> int:
        return int(round(float(pt_val) * (dpi / 72.0)))

    @classmethod
    def get_font(cls, font_family: str, font_size_pt: float, bold: bool, dpi: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        font_size_px = max(10, cls.pt_to_px(font_size_pt, dpi))
        fonts_dir = Path(settings.BASE_DIR) / "assets" / "fonts"

        candidates = []
        if font_family in ("Gilroy-Bold", "Gilroy-Regular"):
            candidates.append(fonts_dir / f"{font_family}.ttf")

        if bold or "Bold" in font_family:
            candidates.extend([Path(p) for p in cls.SYSTEM_SANS_BOLD])
        elif font_family == "Courier":
            candidates.extend([Path(p) for p in cls.SYSTEM_MONO])
        else:
            candidates.extend([Path(p) for p in cls.SYSTEM_SANS])

        for path in candidates:
            if path.is_file():
                try:
                    return ImageFont.truetype(str(path), font_size_px)
                except Exception:
                    pass

        try:
            return ImageFont.load_default()
        except Exception:
            return None

    @classmethod
    def parse_color(cls, color_str: str | None, default=(0, 0, 0, 255)) -> tuple[int, int, int, int] | None:
        if not color_str:
            return None
        try:
            rgb = ImageColor.getrgb(str(color_str))
            if len(rgb) == 3:
                return (*rgb, 255)
            return rgb
        except Exception:
            return default

    @classmethod
    def render_png(
        cls,
        width_mm: float | Decimal,
        height_mm: float | Decimal,
        layout: dict[str, Any],
        context: dict[str, Any],
        dpi: int = DEFAULT_DPI,
    ) -> bytes:
        """
        Renders single label PNG preview stream.
        Returns binary PNG bytes.
        """
        try:
            t_width_mm = float(width_mm)
            t_height_mm = float(height_mm)

            img_w = cls.mm_to_px(t_width_mm, dpi)
            img_h = cls.mm_to_px(t_height_mm, dpi)

            bg_hex = layout.get("background_color", "#FFFFFF")
            bg_rgba = cls.parse_color(bg_hex, default=(255, 255, 255, 255))

            image = Image.new("RGBA", (img_w, img_h), bg_rgba)
            draw = ImageDraw.Draw(image)

            elements = layout.get("elements", [])
            sorted_elements = sorted(elements, key=lambda el: el.get("z_index", 0))

            for elem in sorted_elements:
                if not elem.get("visible", True):
                    continue
                cls._draw_element(image, draw, elem, context, dpi)

            buffer = io.BytesIO()
            image.save(buffer, format="PNG", dpi=(dpi, dpi))
            buffer.seek(0)
            return buffer.getvalue()
        except Exception as e:
            raise LabelRenderingError(f"PNG preview generatsiya qilishda xatolik yuz berdi: {str(e)}") from e

    @classmethod
    def _draw_element(
        cls,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        elem: dict[str, Any],
        context: dict[str, Any],
        dpi: int,
    ):
        elem_type = elem.get("type")
        x_px = cls.mm_to_px(elem.get("x_mm", 0), dpi)
        y_px = cls.mm_to_px(elem.get("y_mm", 0), dpi)
        w_px = cls.mm_to_px(elem.get("width_mm", 0), dpi)
        h_px = cls.mm_to_px(elem.get("height_mm", 0), dpi)

        if elem_type == "shape":
            cls._draw_shape(draw, elem, x_px, y_px, w_px, h_px, dpi)
        elif elem_type == "line":
            cls._draw_line(draw, elem, x_px, y_px, w_px, h_px)
        elif elem_type == "text":
            cls._draw_text(draw, elem, context, x_px, y_px, w_px, h_px, dpi)
        elif elem_type == "barcode":
            cls._draw_barcode(draw, elem, context, x_px, y_px, w_px, h_px, dpi)
        elif elem_type == "image":
            cls._draw_image(image, elem, context, x_px, y_px, w_px, h_px)

    @classmethod
    def _draw_shape(
        cls,
        draw: ImageDraw.ImageDraw,
        elem: dict[str, Any],
        x: int,
        y: int,
        w: int,
        h: int,
        dpi: int,
    ):
        bg_rgba = cls.parse_color(elem.get("bg_color"), default=None)
        border_rgba = cls.parse_color(elem.get("border_color"), default=None)
        border_w = cls.mm_to_px(elem.get("border_width_mm", 0), dpi)
        corner_r = cls.mm_to_px(elem.get("corner_radius_mm", 0), dpi)

        if not bg_rgba and not border_rgba:
            return

        box = [x, y, x + w, y + h]
        outline = border_rgba if (border_rgba and border_w > 0) else None

        if corner_r > 0:
            draw.rounded_rectangle(box, radius=corner_r, fill=bg_rgba, outline=outline, width=max(1, border_w))
        else:
            draw.rectangle(box, fill=bg_rgba, outline=outline, width=max(1, border_w) if outline else 0)

    @classmethod
    def _draw_line(cls, draw: ImageDraw.ImageDraw, elem: dict[str, Any], x: int, y: int, w: int, h: int):
        color_rgba = cls.parse_color(elem.get("color"), default=(0, 0, 0, 255))
        draw.rectangle([x, y, x + w, y + h], fill=color_rgba)

    @classmethod
    def _draw_text(
        cls,
        draw: ImageDraw.ImageDraw,
        elem: dict[str, Any],
        context: dict[str, Any],
        x: int,
        y: int,
        w: int,
        h: int,
        dpi: int,
    ):
        field = elem.get("field")
        text = str(context.get(field, "") if field else elem.get("static_value", ""))
        if not text:
            return

        elem_bg_rgba = cls.parse_color(elem.get("bg_color"), default=None)
        if elem_bg_rgba:
            draw.rectangle([x, y, x + w, y + h], fill=elem_bg_rgba)

        font_family = elem.get("font_family", "Helvetica")
        bold = bool(elem.get("bold", False))
        font_size_pt = float(elem.get("font_size", 8.0))
        font = cls.get_font(font_family, font_size_pt, bold, dpi)
        if not font:
            return

        color_rgba = cls.parse_color(elem.get("color"), default=(0, 0, 0, 255))

        # Truncate text if needed to fit inside w
        fitted_text = text
        bbox = draw.textbbox((0, 0), fitted_text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        if text_w > w:
            for i in range(len(text), 0, -1):
                sub = text[:i] + "..."
                bbox = draw.textbbox((0, 0), sub, font=font)
                tw = bbox[2] - bbox[0]
                if tw <= w:
                    fitted_text = sub
                    text_w = tw
                    text_h = bbox[3] - bbox[1]
                    break

        align = elem.get("align", "left")
        if align == "center":
            tx = x + (w - text_w) // 2
        elif align == "right":
            tx = x + w - text_w
        else:
            tx = x

        # Vertically center or top align inside h
        ty = y + max(0, (h - text_h) // 2) - bbox[1]

        draw.text((tx, ty), fitted_text, font=font, fill=color_rgba)

        if elem.get("underline", False):
            line_y = y + h - max(2, cls.pt_to_px(1, dpi))
            draw.line([(tx, line_y), (tx + text_w, line_y)], fill=color_rgba, width=max(1, cls.pt_to_px(1, dpi)))

    @classmethod
    def _draw_barcode(
        cls,
        draw: ImageDraw.ImageDraw,
        elem: dict[str, Any],
        context: dict[str, Any],
        x: int,
        y: int,
        w: int,
        h: int,
        dpi: int,
    ):
        raw_barcode = context.get("product.barcode") or "2000000000000"
        barcode_res = BarcodeGeneratorService.generate(str(raw_barcode))

        barcode_opts = elem.get("barcode_options") or {}
        show_text = bool(barcode_opts.get("show_text", True))
        font_size_pt = float(barcode_opts.get("font_size", 5.5))

        font = cls.get_font("Helvetica", font_size_pt, False, dpi)
        font_size_px = cls.pt_to_px(font_size_pt, dpi)

        if show_text and h > (font_size_px + 10):
            text_area_h = font_size_px + cls.mm_to_px(0.5, dpi)
            bars_h = h - text_area_h
            guard_drop = min(int(text_area_h * 0.45), cls.mm_to_px(1.0, dpi))
        else:
            show_text = False
            bars_h = h
            guard_drop = 0

        total_modules = barcode_res.total_modules
        mod_w_float = float(w) / float(total_modules)

        # Draw barcode bars
        black_color = (0, 0, 0, 255)
        for idx, module in enumerate(barcode_res.modules):
            if module.is_black:
                mx0 = int(round(x + idx * mod_w_float))
                mx1 = int(round(x + (idx + 1) * mod_w_float))
                if mx1 <= mx0:
                    mx1 = mx0 + 1
                bar_actual_h = bars_h + (guard_drop if module.is_guard else 0)
                draw.rectangle([mx0, y, mx1, y + bar_actual_h], fill=black_color)

        if show_text and font:
            first_digit = barcode_res.barcode_value[0]
            left_part = barcode_res.barcode_value[1:7]
            right_part = barcode_res.barcode_value[7:13]

            text_y = y + bars_h + cls.mm_to_px(0.2, dpi)

            # Left 6 digits centered under modules 3..45
            l_center_x = x + int(round((3 + 45) / 2.0 * mod_w_float))
            l_bbox = draw.textbbox((0, 0), left_part, font=font)
            l_w = l_bbox[2] - l_bbox[0]
            draw.text((l_center_x - l_w // 2, text_y), left_part, font=font, fill=black_color)

            # Right 6 digits centered under modules 50..92
            r_center_x = x + int(round((50 + 92) / 2.0 * mod_w_float))
            r_bbox = draw.textbbox((0, 0), right_part, font=font)
            r_w = r_bbox[2] - r_bbox[0]
            draw.text((r_center_x - r_w // 2, text_y), right_part, font=font, fill=black_color)

            # First digit on the left margin
            f_bbox = draw.textbbox((0, 0), first_digit, font=font)
            f_w = f_bbox[2] - f_bbox[0]
            if x >= f_w + 2:
                draw.text((x - f_w - 2, text_y), first_digit, font=font, fill=black_color)

    @classmethod
    def _draw_image(
        cls,
        image: Image.Image,
        elem: dict[str, Any],
        context: dict[str, Any],
        x: int,
        y: int,
        w: int,
        h: int,
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

        try:
            with Image.open(file_path) as loaded_img:
                loaded_img = loaded_img.convert("RGBA")
                orig_w, orig_h = loaded_img.size

            if orig_w <= 0 or orig_h <= 0 or w <= 0 or h <= 0:
                return

            fit = elem.get("image_fit", "contain")
            aspect = orig_w / orig_h
            box_aspect = w / h

            if fit == "contain":
                if aspect > box_aspect:
                    nw = w
                    nh = max(1, int(w / aspect))
                else:
                    nh = h
                    nw = max(1, int(h * aspect))
                nx = x + (w - nw) // 2
                ny = y + (h - nh) // 2
            else:
                nx, ny, nw, nh = x, y, w, h

            resized = loaded_img.resize((nw, nh), Image.Resampling.LANCZOS)
            image.alpha_composite(resized, dest=(nx, ny))
        except Exception:
            pass
