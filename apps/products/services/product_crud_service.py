from django.db import transaction
from django.core.exceptions import ValidationError
from apps.products.models import Product



class ProductService:

    @staticmethod
    @transaction.atomic
    def create_product(data: dict):

        from apps.products.utils.barcode_utility import generate_unique_barcode, generate_barcode_image

        # 🔴 VALIDATIONS
        if data.get("price") <= 0:
            raise ValidationError("Narx ijobiy bo'lishi kerak")

        if data.get("quantity") < 0:
            raise ValidationError("Miqdor salbiy bo'lishi mumkin emas")

        # ✅ barcode generate
        barcode_number = generate_unique_barcode()
        data["barcode"] = barcode_number

        # ✅ create product
        product = Product.objects.create(**data)

        # ✅ generate barcode image
        image_file = generate_barcode_image(barcode_number)
        product.shtrix_code.save(image_file.name, image_file, save=True)

        return product