"""
Mahsulot miqdori (quantity) uchun markaziy qoidalar.

Juft mahsulot (Product.is_pair=True, masalan fara — 2 dona = 1 juft, 1 dona = 0.5 juft, 0.5 dona = 0.25 juft)
0.25 qadam bilan sotilishi/qaytarilishi/ko'chirilishi/kirim qilinishi mumkin.
Oddiy mahsulotda (Product.is_pair=False) miqdor faqat BUTUN son bo'ladi.
Barcha kirish nuqtalari (sotuv, qaytarish, kirim, transfer, spisaniye, inventarizatsiya)
AYNAN shu moduldagi validatsiyani chaqiradi — qoida boshqa joyda takrorlanmaydi.

Saqlash: barcha quantity ustunlari DecimalField(max_digits=12, decimal_places=2),
lekin qiymat juft mahsulotda 0.25 ga, oddiy mahsulotda 1 ga karrali (validate_quantity_step kafolatlaydi).
"""

from decimal import Decimal, InvalidOperation

from rest_framework import serializers
from rest_framework.exceptions import ValidationError

# Quantity ustunlarining yagona spetsifikatsiyasi (model va serializer mos bo'lishi shart)
QUANTITY_MAX_DIGITS = 12
QUANTITY_DECIMAL_PLACES = 2

PAIR_STEP = Decimal("0.25")
SINGLE_STEP = Decimal("1")
HALF_STEP = Decimal("0.5")  # backward compatibility alias


def is_product_pair(product) -> bool:
    """
    Mahsulotning juft (0.25 qadam) yoki dona (1 qadam) ekanligini aniqlaydi.
    Asosiy manba: product.unit_measurement.quantity_type ('QUARTER' -> True, 'WHOLE' -> False).
    Fallback: product.is_pair (mavjud legacy kod/testlar uchun).
    """
    if product is None:
        return False
    unit = getattr(product, "unit_measurement", None)
    if unit is not None and hasattr(unit, "quantity_type"):
        return unit.quantity_type == "QUARTER"
    if hasattr(product, "is_pair_effective"):
        return bool(product.is_pair_effective)
    return bool(getattr(product, "is_pair", False))


def get_quantity_step(product_or_unit) -> Decimal:
    """Mahsulot yoki o'lchov birligining qadamini (0.25 yoki 1) qaytaradi."""
    if product_or_unit is None:
        return SINGLE_STEP
    if hasattr(product_or_unit, "quantity_type"):
        return PAIR_STEP if product_or_unit.quantity_type == "QUARTER" else SINGLE_STEP
    return PAIR_STEP if is_product_pair(product_or_unit) else SINGLE_STEP


def as_quantity(value) -> Decimal:
    """JSON'dan kelgan son/matnni xavfsiz Decimal ga o'giradi (float artefaktlarisiz)."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Miqdor noto'g'ri formatda")


def validate_quantity_step(
    quantity,
    *,
    is_pair: bool | None = None,
    product=None,
    unit=None,
    product_name: str = "",
    allow_zero: bool = False,
) -> Decimal:
    """
    Miqdor qadamini tekshiradi va normallashtirilgan Decimal qaytaradi.

      is_pair=True / unit.quantity_type='QUARTER' → 0.25 ga karrali bo'lishi kerak (0.25, 0.50, 0.75, 1.00, ...)
      is_pair=False / unit.quantity_type='WHOLE'   → faqat butun son (1, 2, 3, 4, ...)

    allow_zero=True — inventarizatsiya sanog'i kabi 0 qiymat joiz bo'lgan joylar uchun.
    """
    if unit is not None and hasattr(unit, "quantity_type"):
        pair_flag = (unit.quantity_type == "QUARTER")
    elif product is not None:
        pair_flag = is_product_pair(product)
        if not product_name:
            product_name = getattr(product, "name", "")
    elif is_pair is not None:
        pair_flag = is_pair
    else:
        pair_flag = False

    qty = as_quantity(quantity)
    name = f"{product_name}: " if product_name else ""

    if qty < 0 or (qty == 0 and not allow_zero):
        raise ValidationError(f"{name}miqdor 0 dan katta bo'lishi kerak")

    if pair_flag:
        if (qty * 4) % 1 != 0:
            raise ValidationError(
                f"{name}miqdor 0.25 ga karrali bo'lishi kerak (0.25, 0.5, 0.75, 1.0 ...)"
            )
    else:
        if qty % 1 != 0:
            raise ValidationError(
                f"{name}bu mahsulot juft mahsulot emas — miqdor butun son bo'lishi kerak"
            )

    return qty


def validate_items_quantity_steps(items, products_by_id, *, product_key="product", quantity_key="quantity"):
    """
    Ro'yxatdagi har bir {product, quantity} satrini tegishli mahsulot/unit
    bo'yicha tekshiradi. products_by_id — {id: Product} xaritasi.
    Satrdagi product qiymati id yoki Product instance bo'lishi mumkin.
    """
    for item in items:
        product = item[product_key]
        if not hasattr(product, "is_pair") and not hasattr(product, "unit_measurement"):
            product = products_by_id.get(product)
        if product is None:
            # Mahsulot topilmasa asosiy servis o'zi xato beradi — bu yerda qadam tekshirilmaydi
            continue
        item[quantity_key] = validate_quantity_step(
            item[quantity_key], product=product, product_name=getattr(product, "name", "")
        )


class QuantityField(serializers.DecimalField):
    """
    Quantity uchun standart DRF maydoni:
      - JSON'da raqam sifatida chiqadi (coerce_to_string=False) — client
        arifmetikasi stringga yiqilmasligi uchun;
      - 0.25 ga karralilikni maydon darajasida tekshiradi (mahsulotga bog'liq
        butun-son talabi esa servis/serializer validate'ida, chunki u Product.is_pair ni bilishi kerak).
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", QUANTITY_MAX_DIGITS)
        kwargs.setdefault("decimal_places", QUANTITY_DECIMAL_PLACES)
        kwargs.setdefault("coerce_to_string", False)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if (value * 4) % 1 != 0:
            raise serializers.ValidationError(
                "Miqdor 0.25 ga karrali bo'lishi kerak (0.25, 0.5, 0.75, 1.0 ...)"
            )
        return value
