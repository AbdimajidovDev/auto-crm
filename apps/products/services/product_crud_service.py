from django.db import transaction
from django.core.exceptions import ValidationError
from apps.products.models import Product, ProductImage


from django.db import transaction


class ProductService:

    @staticmethod
    @transaction.atomic
    def create_product(data: dict):

        # HAR QANDAY HOLATDA LIST BO‘LADI
        images = data.pop("images", None) or []

        # 1. product create
        product = Product.objects.create(**data)

        # 2. images create (SAFE)
        for image in images:
            ProductImage.objects.create(
                product=product,
                image=image
            )

        return product



from io import BytesIO

import barcode
from barcode.writer import ImageWriter


class BarcodeService:

    @staticmethod
    def generate(barcode_number: str):

        buffer = BytesIO()

        generator = barcode.get(
            "ean13",
            barcode_number,
            writer=ImageWriter()
        )

        generator.write(
            buffer,
            options={
                "write_text": True,
            }
        )

        buffer.seek(0)

        return buffer