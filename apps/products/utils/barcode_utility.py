import logging
import random
import barcode
from barcode.writer import ImageWriter
from django.core.files.base import ContentFile
from io import BytesIO

logger = logging.getLogger(__name__)



def generate_unique_barcode():
    from apps.products.models import Product

    while True:
        code = ''.join(str(random.randint(0, 9)) for _ in range(12))

        ean = barcode.get_barcode_class("ean13")
        full_code = ean(code).get_fullcode()

        if not Product.objects.filter(barcode=full_code).exists():
            return full_code


def generate_barcode_image(barcode_number: str):
    """
    Barcode uchun PNG rasm yaratadi.

    Qiymat yaroqli EAN-13 bo'lmasa None qaytaradi (istisno ko'tarmaydi):
    shtrix rasm — qulaylik artefakti, uning yaratilmasligi butun mahsulot
    yaratishni 500 bilan yiqitmasligi kerak.
    """
    EAN = barcode.get_barcode_class('ean13')
    buffer = BytesIO()
    try:
        ean = EAN(barcode_number, writer=ImageWriter())
        ean.write(buffer, options={"write_text": True})
    except Exception:
        logger.warning("Barcode rasmi yaratilmadi (yaroqsiz EAN-13): %r", barcode_number)
        return None
    return ContentFile(buffer.getvalue(), name=f"{barcode_number}.png")


def normalize_barcode(value: str) -> str:
    """
    Qo'lda kiritilgan barcode'ni EAN-13 formatga keltiradi
    (12-13 raqam -> checksum bilan 13 raqam).
    Bu self.barcode bilan generatsiya qilinadigan shtrix rasm mos kelishini kafolatlaydi.
    Yaroqsiz qiymatda ValueError ko'taradi.
    """
    value = (value or "").strip()
    if not value.isdigit():
        raise ValueError("Barcode faqat raqamlardan iborat bo'lishi kerak.")
    # EAN-13 13 dan uzun qiymatni jim-jit kesib oladi — buni oldini olamiz.
    if len(value) not in (12, 13):
        raise ValueError("Barcode 12 yoki 13 raqamdan iborat bo'lishi kerak.")
    ean = barcode.get_barcode_class("ean13")
    try:
        return ean(value).get_fullcode()
    except Exception as e:
        # python-barcode xatolarini (NumberOfDigitsError va h.k.) ValueError'ga keltiramiz,
        # shunda chaqiruvchilar yagona `except ValueError` bilan ushlay oladi.
        raise ValueError("Barcode yaroqli EAN-13 formatda bo'lishi kerak (12-13 raqam).") from e
