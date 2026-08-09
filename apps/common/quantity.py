"""
Mahsulot miqdori (quantity) uchun markaziy qoidalar.

Juft mahsulot (Product.is_pair=True, masalan fara — 2 dona = 1 juft) YARIM
(0.5) qadam bilan sotilishi/qaytarilishi/ko'chirilishi mumkin. Oddiy mahsulotda
miqdor faqat BUTUN son bo'ladi. Barcha kirish nuqtalari (sotuv, qaytarish,
kirim, transfer, spisaniye, inventarizatsiya) AYNAN shu moduldagi
validatsiyani chaqiradi — qoida boshqa joyda takrorlanmaydi.

Saqlash: barcha quantity ustunlari DecimalField(max_digits=12, decimal_places=2),
lekin qiymat har doim 0.5 ga karrali (validate_quantity_step kafolatlaydi).
"""

from decimal import Decimal, InvalidOperation

from rest_framework import serializers
from rest_framework.exceptions import ValidationError

# Quantity ustunlarining yagona spetsifikatsiyasi (model va serializer mos bo'lishi shart)
QUANTITY_MAX_DIGITS = 12
QUANTITY_DECIMAL_PLACES = 2

HALF_STEP = Decimal("0.5")


def as_quantity(value) -> Decimal:
    """JSON'dan kelgan son/matnni xavfsiz Decimal ga o'giradi (float artefaktlarisiz)."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Miqdor noto'g'ri formatda")


def validate_quantity_step(quantity, *, is_pair: bool, product_name: str = "", allow_zero: bool = False) -> Decimal:
    """
    Miqdor qadamini tekshiradi va normallashtirilgan Decimal qaytaradi.

      is_pair=True  → 0.5 ga karrali bo'lishi kerak (0.5 = yarim juft)
      is_pair=False → faqat butun son

    allow_zero=True — inventarizatsiya sanog'i kabi 0 qiymat joiz bo'lgan joylar uchun.
    """
    qty = as_quantity(quantity)
    name = f"{product_name}: " if product_name else ""

    if qty < 0 or (qty == 0 and not allow_zero):
        raise ValidationError(f"{name}miqdor 0 dan katta bo'lishi kerak")

    if (qty * 2) % 1 != 0:
        raise ValidationError(
            f"{name}miqdor 0.5 ga karrali bo'lishi kerak (yarim juft = 0.5)"
        )

    if not is_pair and qty % 1 != 0:
        raise ValidationError(
            f"{name}bu mahsulot juft mahsulot emas — miqdor butun son bo'lishi kerak"
        )

    return qty


def validate_items_quantity_steps(items, products_by_id, *, product_key="product", quantity_key="quantity"):
    """
    Ro'yxatdagi har bir {product, quantity} satrini tegishli Product.is_pair
    bo'yicha tekshiradi. products_by_id — {id: Product} xaritasi.
    Satrdagi product qiymati id yoki Product instance bo'lishi mumkin.
    """
    for item in items:
        product = item[product_key]
        if not hasattr(product, "is_pair"):
            product = products_by_id.get(product)
        if product is None:
            # Mahsulot topilmasa asosiy servis o'zi xato beradi — bu yerda qadam tekshirilmaydi
            continue
        item[quantity_key] = validate_quantity_step(
            item[quantity_key], is_pair=product.is_pair, product_name=product.name
        )


class QuantityField(serializers.DecimalField):
    """
    Quantity uchun standart DRF maydoni:
      - JSON'da raqam sifatida chiqadi (coerce_to_string=False) — client
        arifmetikasi stringga yiqilmasligi uchun;
      - 0.5 ga karralilikni maydon darajasida tekshiradi (mahsulotga bog'liq
        butun-son talabi esa servis/serializer validate'ida, chunki u Product.is_pair ni bilishi kerak).
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", QUANTITY_MAX_DIGITS)
        kwargs.setdefault("decimal_places", QUANTITY_DECIMAL_PLACES)
        kwargs.setdefault("coerce_to_string", False)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if (value * 2) % 1 != 0:
            raise serializers.ValidationError(
                "Miqdor 0.5 ga karrali bo'lishi kerak (yarim juft = 0.5)"
            )
        return value
