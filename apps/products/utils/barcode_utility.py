import random
import barcode
from barcode.writer import ImageWriter
from django.core.files.base import ContentFile
from io import BytesIO

from apps.products.models import ProductBatch



def generate_unique_barcode():
    while True:
        code = ''.join([str(random.randint(0, 9)) for _ in range(12)])
        if not ProductBatch.objects.filter(barcode=code).exists():
            return code


def generate_barcode_image(barcode_number: str):
    EAN = barcode.get_barcode_class('ean13')

    buffer = BytesIO()
    ean = EAN(barcode_number, writer=ImageWriter())

    ean.write(buffer, options={
        "write_text": True
    })

    return ContentFile(buffer.getvalue(), name=f"{barcode_number}.png")

