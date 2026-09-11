import re
from typing import Any
from decimal import Decimal
from PIL import Image

from .exceptions import LabelValidationError
from .resolver import LabelDataResolver


HEX_COLOR_REGEX = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")

ALLOWED_ELEMENT_TYPES = {"text", "barcode", "image", "shape", "line"}

ALLOWED_FIELDS = {
    "product.name",
    "product.sku",
    "product.barcode",
    "product.brand.name",
    "product.brand.logo",
    "product.category.name",
    "product.unit",
    "product.image",
    "product.batch.selling_price",
    "company.name",
    "company.logo",
}

ALLOWED_IMAGE_FIELDS = {
    "company.logo",
    "product.brand.logo",
    "product.image",
}

ALLOWED_FONTS = {
    "Gilroy-Bold",
    "Gilroy-Regular",
    "Helvetica-Bold",
    "Helvetica",
    "Courier",
}

ALLOWED_ALIGNS = {"left", "center", "right"}
ALLOWED_IMAGE_FITS = {"contain", "cover", "fill"}


class LabelTemplateValidator:
    """
    Validates barcode label layout JSON and enforces canonical constraints:
    - Single source of truth (width/height not in JSON)
    - Boundary checks
    - Unique element IDs
    - Field whitelisting
    - Image security (no arbitrary URLs)
    - Advisory warnings for barcode readability
    """

    @classmethod
    def validate(
        cls,
        layout: dict[str, Any],
        width_mm: float | Decimal,
        height_mm: float | Decimal,
    ) -> tuple[dict[str, Any], list[str]]:
        if not isinstance(layout, dict):
            raise LabelValidationError("Layout obyekt (dict) ko'rinishida bo'lishi shart.")

        # 1. Single source of truth enforcement
        if "width_mm" in layout or "height_mm" in layout:
            raise LabelValidationError(
                "Single source of truth buzildi: width_mm va height_mm maydonlari layout JSON ichida "
                "berilmasligi shart. Ular faqat shablon model ustunlarida saqlanadi."
            )

        # 2. Version check
        version = layout.get("version", 1)
        if version != 1:
            raise LabelValidationError(f"Noma'lum layout versiyasi: {version}. Faqat 1-versiya qo'llab-quvvatlanadi.")

        # 3. Background color check
        bg_color = layout.get("background_color", "#FFFFFF")
        if bg_color and not HEX_COLOR_REGEX.match(str(bg_color)):
            raise LabelValidationError(f"Noto'g'ri fon rangi (HEX format talab qilinadi): {bg_color}")

        # 4. Elements array check
        elements = layout.get("elements")
        if not isinstance(elements, list):
            raise LabelValidationError("Layout ichida 'elements' massivi bo'lishi shart.")

        t_width = float(width_mm)
        t_height = float(height_mm)
        seen_ids = set()
        warnings = []
        cleaned_elements = []

        epsilon = 0.05  # 0.05 mm float tolerance

        for idx, elem in enumerate(elements):
            if not isinstance(elem, dict):
                raise LabelValidationError(f"Element #{idx} noto'g'ri formatda.")

            elem_id = elem.get("id")
            if not elem_id or not isinstance(elem_id, str) or not elem_id.strip():
                raise LabelValidationError(f"Element #{idx} uchun 'id' (unikal matn) ko'rsatilishi shart.")
            elem_id = elem_id.strip()

            if elem_id in seen_ids:
                raise LabelValidationError(f"Element ID lari takrorlanmas (unique) bo'lishi shart. '{elem_id}' bir necha marta ishlatilgan.")
            seen_ids.add(elem_id)

            elem_type = elem.get("type")
            if elem_type not in ALLOWED_ELEMENT_TYPES:
                raise LabelValidationError(f"Element '{elem_id}': Noma'lum tur '{elem_type}'. Ruxsat etilgan turlar: {ALLOWED_ELEMENT_TYPES}")

            # Coordinates
            try:
                x_mm = float(elem.get("x_mm", 0))
                y_mm = float(elem.get("y_mm", 0))
                w_mm = float(elem.get("width_mm", 0))
                h_mm = float(elem.get("height_mm", 0))
            except (ValueError, TypeError):
                raise LabelValidationError(f"Element '{elem_id}': x_mm, y_mm, width_mm, height_mm son bo'lishi shart.")

            if x_mm < 0 or y_mm < 0:
                raise LabelValidationError(f"Element '{elem_id}': Koordinatalar (x_mm, y_mm) manfiy bo'lishi mumkin emas.")

            if w_mm <= 0 or h_mm <= 0:
                raise LabelValidationError(f"Element '{elem_id}': width_mm va height_mm 0 dan katta bo'lishi shart.")

            # Boundary check
            if (x_mm + w_mm) > (t_width + epsilon):
                raise LabelValidationError(
                    f"Element '{elem_id}' eni yorliq chegarasidan oshib ketdi: "
                    f"x ({x_mm}) + width ({w_mm}) = {x_mm + w_mm:.2f} mm > shablon eni ({t_width:.2f} mm)."
                )

            if (y_mm + h_mm) > (t_height + epsilon):
                raise LabelValidationError(
                    f"Element '{elem_id}' bo'yi yorliq chegarasidan oshib ketdi: "
                    f"y ({y_mm}) + height ({h_mm}) = {y_mm + h_mm:.2f} mm > shablon bo'yi ({t_height:.2f} mm)."
                )

            z_index = int(elem.get("z_index", 0))
            visible = bool(elem.get("visible", True))

            field = elem.get("field")
            if field is not None:
                if not isinstance(field, str) or field not in ALLOWED_FIELDS:
                    raise LabelValidationError(
                        f"Element '{elem_id}': Noma'lum maydon (field) '{field}'. "
                        f"Ruxsat etilgan maydonlar: {sorted(list(ALLOWED_FIELDS))}"
                    )

            cleaned_elem: dict[str, Any] = {
                "id": elem_id,
                "type": elem_type,
                "x_mm": round(x_mm, 2),
                "y_mm": round(y_mm, 2),
                "width_mm": round(w_mm, 2),
                "height_mm": round(h_mm, 2),
                "z_index": z_index,
                "visible": visible,
                "field": field,
                "static_value": None,
            }

            # Type-specific validation & security
            if elem_type == "image":
                source_type = elem.get("source_type", "field")
                if source_type not in ("field", "uploaded"):
                    raise LabelValidationError(
                        f"Element '{elem_id}': Noma'lum rasm manbasi turi (source_type): '{source_type}'. "
                        "Faqat 'field' yoki 'uploaded' bo'lishi mumkin."
                    )

                static_val = elem.get("static_value")
                if static_val:
                    raise LabelValidationError(
                        f"Element '{elem_id}': Rasm elementiga static_value orqali ixtiyoriy URL yoki fayl yo'li "
                        "berish xavfsizlik nuqtai nazaridan taqiqlangan."
                    )

                if source_type == "field":
                    if not field or field not in ALLOWED_IMAGE_FIELDS:
                        raise LabelValidationError(
                            f"Element '{elem_id}': Rasm elementi uchun 'field' faqat quyidagilardan biri bo'lishi shart: "
                            f"{sorted(list(ALLOWED_IMAGE_FIELDS))}. '{field}' qabul qilinmaydi."
                        )
                    cleaned_elem["source_type"] = "field"
                    cleaned_elem["field"] = field
                    cleaned_elem["image_url"] = None
                else:  # source_type == "uploaded"
                    image_url = elem.get("image_url")
                    if not image_url or not isinstance(image_url, str) or not image_url.strip():
                        raise LabelValidationError(
                            f"Element '{elem_id}': Qurilmadan yuklangan rasm uchun 'image_url' berilishi shart."
                        )
                    image_url = image_url.strip()

                    file_path = LabelDataResolver.resolve_uploaded_image_path(image_url)
                    if not file_path:
                        raise LabelValidationError(
                            f"Element '{elem_id}': Ko'rsatilgan rasm serverda topilmadi yoki xavfsizlik talablariga javob bermaydi: {image_url}"
                        )

                    try:
                        with Image.open(file_path) as test_img:
                            if test_img.format not in {"JPEG", "PNG", "WEBP"}:
                                raise LabelValidationError(
                                    f"Element '{elem_id}': Rasm formati faqat JPG, PNG yoki WEBP bo'lishi kerak."
                                )
                            test_img.verify()
                    except Exception as e:
                        if isinstance(e, LabelValidationError):
                            raise
                        raise LabelValidationError(f"Element '{elem_id}': Rasm fayli buzilgan yoki ochib bo'lmadi.")

                    cleaned_elem["source_type"] = "uploaded"
                    cleaned_elem["field"] = None
                    cleaned_elem["image_url"] = image_url

                image_fit = elem.get("image_fit", "contain")
                if image_fit not in ALLOWED_IMAGE_FITS:
                    image_fit = "contain"
                cleaned_elem["image_fit"] = image_fit

            elif elem_type == "barcode":
                if field != "product.barcode":
                    cleaned_elem["field"] = "product.barcode"

                barcode_options = elem.get("barcode_options") or {}
                cleaned_elem["barcode_options"] = {
                    "show_text": bool(barcode_options.get("show_text", True)),
                    "font_size": float(barcode_options.get("font_size", 6.0)),
                }

                # Advisory optical readability warnings
                if w_mm < 20.0 or h_mm < 6.0:
                    warnings.append(
                        f"Element '{elem_id}': Shtrix-kod o'lchami 20x6 mm dan kichik ({w_mm:.1f}x{h_mm:.1f} mm). "
                        "Ayrim optik skanerlar qiyinchilik bilan o'qishi mumkin."
                    )
                if x_mm < 1.5 or (t_width - (x_mm + w_mm)) < 1.5:
                    warnings.append(
                        f"Element '{elem_id}': Shtrix-kod atrofida kamida 1.5 mm bo'sh hudud (quiet zone) qoldirish tavsiya etiladi."
                    )

            elif elem_type == "text":
                static_val = elem.get("static_value")
                if static_val is not None:
                    cleaned_elem["static_value"] = str(static_val)

                font_family = elem.get("font_family", "Helvetica")
                if font_family not in ALLOWED_FONTS:
                    font_family = "Helvetica"
                cleaned_elem["font_family"] = font_family

                font_size = float(elem.get("font_size", 8.0))
                font_size = max(4.0, min(36.0, font_size))
                cleaned_elem["font_size"] = font_size

                cleaned_elem["bold"] = bool(elem.get("bold", False))
                cleaned_elem["italic"] = bool(elem.get("italic", False))
                cleaned_elem["underline"] = bool(elem.get("underline", False))

                align = elem.get("align", "left")
                if align not in ALLOWED_ALIGNS:
                    align = "left"
                cleaned_elem["align"] = align

                color = elem.get("color", "#000000")
                if color and not HEX_COLOR_REGEX.match(str(color)):
                    color = "#000000"
                cleaned_elem["color"] = color

                bg_color_el = elem.get("bg_color")
                if bg_color_el and not HEX_COLOR_REGEX.match(str(bg_color_el)):
                    bg_color_el = None
                cleaned_elem["bg_color"] = bg_color_el

            elif elem_type == "shape":
                bg = elem.get("bg_color")
                if bg and not HEX_COLOR_REGEX.match(str(bg)):
                    bg = None
                cleaned_elem["bg_color"] = bg

                border_color = elem.get("border_color")
                if border_color and not HEX_COLOR_REGEX.match(str(border_color)):
                    border_color = None
                cleaned_elem["border_color"] = border_color
                cleaned_elem["border_width_mm"] = max(0.0, float(elem.get("border_width_mm", 0)))
                cleaned_elem["corner_radius_mm"] = max(0.0, float(elem.get("corner_radius_mm", 0)))

            elif elem_type == "line":
                color = elem.get("color", "#000000")
                if color and not HEX_COLOR_REGEX.match(str(color)):
                    color = "#000000"
                cleaned_elem["color"] = color
                cleaned_elem["border_width_mm"] = max(0.1, float(elem.get("border_width_mm", 0.5)))

            cleaned_elements.append(cleaned_elem)

        cleaned_layout = {
            "version": 1,
            "background_color": bg_color,
            "elements": cleaned_elements,
        }
        return cleaned_layout, warnings
