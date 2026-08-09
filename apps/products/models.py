from django.db import models
from django.utils.text import slugify

from apps.common.models.timestamp_mixin import TimestampMixin
from apps.products.utils.barcode_utility import generate_unique_barcode, generate_barcode_image
from apps.products.utils.path_utility import product_image_path, product_barcode_path
from apps.store.models import Store


# Create your models here.


class Category(TimestampMixin):
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    name = models.CharField(max_length=100)
    description = models.TextField()
    image = models.ImageField(upload_to='categories/', blank=True)

    def save(self, *args, **kwargs):
        self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        db_table = 'category'
        # ordering = ['name']
        verbose_name_plural = 'Categories'



class Brand(models.Model):
        name = models.CharField(
            max_length=100,
            unique=True,
            db_index=True,
        )

        def __str__(self):
            return f"{self.name}"

        class Meta:
            db_table = "brand"
            ordering = ["name"]


class Product(TimestampMixin):

    class ProductStatus(models.TextChoices):
        ACTIVE = "a", "Active"
        INACTIVE = "i", "Inactive"
        DRAFT = "d", "Draft"

    category = models.ForeignKey(Category, on_delete=models.PROTECT, blank=True, null=True)
    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, blank=True, null=True)

    name = models.CharField(max_length=100)
    unit_measurement = models.ForeignKey("ProductUnitMeasurement", on_delete=models.PROTECT, blank=True, null=True)
    description = models.TextField(blank=True, default="")

    sku = models.CharField(max_length=64, unique=True, db_index=True, editable=False, blank=True, null=True)
    barcode = models.CharField(max_length=13, unique=True, db_index=True, blank=True, null=True)
    shtrix_code = models.ImageField(upload_to=product_barcode_path, blank=True, null=True)
    # shtrix_code = models.ImageField(upload_to=product_barcode_path, blank=True, null=True, editable=False,)

    status = models.CharField(
        max_length=20,
        choices=ProductStatus.choices,
        default=ProductStatus.ACTIVE
    )
    # 0 => threshold monitoring disabled for this product.
    # PositiveIntegerField => DB-level non-negative guarantee (CHECK constraint).
    min_stock = models.PositiveIntegerField(default=0)

    # Juft mahsulot (masalan fara: 2 dona = 1 juft). True bo'lsa miqdor 0.5
    # qadam bilan kiritiladi/sotiladi (0.5 = yarim juft, narxi ham shunga
    # proportsional). Qoidalar apps.common.quantity da markazlashgan.
    is_pair = models.BooleanField(default=False)

    def get_category_prefix(self):
        if not self.category:
            return "PRD"

        words = (
            self.category.name_uz
            .strip()
            .split()
        )

        prefix = "".join(
            word[0].upper()
            for word in words
            if word
        )
        return prefix

    def generate_sku(self):
        prefix = self.get_category_prefix()
        return f"{prefix}-{self.id:06d}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new:
            update_fields = []

            # SKU: qo'lda kelmasa avtomatik generatsiya qilinadi
            if not self.sku:
                self.sku = self.generate_sku()
                update_fields.append("sku")

            # BARCODE: qo'lda kelmasa avtomatik generatsiya qilinadi
            if not self.barcode:
                self.barcode = generate_unique_barcode()
                update_fields.append("barcode")

            # SHTRIX CODE: barcode (qo'lda yoki avtomatik) uchun mos rasm yaratiladi.
            # Yaroqsiz EAN-13 da generate_barcode_image None qaytaradi — bunda
            # mahsulot rasmsiz saqlanadi (ilgari butun save() 500 bilan yiqilardi).
            if self.barcode and not self.shtrix_code:
                image = generate_barcode_image(self.barcode)
                if image is not None:
                    self.shtrix_code.save(
                        f"{self.barcode}.png",
                        image,
                        save=False
                    )
                    update_fields.append("shtrix_code")

            if update_fields:
                super().save(update_fields=update_fields)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        db_table = 'product'
        ordering = ['-created_at']

        indexes = [
            models.Index(fields=["sku"]),
            models.Index(fields=["barcode"]),
            models.Index(fields=["name"]),
        ]


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to=product_image_path)

    def __str__(self):
        return self.product.name

    class Meta:
        db_table = 'product_image'


class ProductBatch(TimestampMixin):
    location = models.ForeignKey("ProductLocation", on_delete=models.PROTECT, blank=True, null=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='batches')
    store = models.ForeignKey(Store, on_delete=models.CASCADE)
    # Decimal: juft mahsulotlar yarim (0.5) qadam bilan sotilishi mumkin,
    # shuning uchun qoldiq kasr bo'lishi mumkin (masalan 9.5 juft)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    wholesale_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "product_batch"
        indexes = [
            models.Index(fields=["store", "product"]),
        ]
        constraints = [
            # Har (do'kon, mahsulot) uchun BITTA partiya — ilova mantig'i shunga
            # tayanadi (transfer/kirim servislari batch'ni .get()/yangilash bilan
            # ishlatadi). Bu migratsiyani tasodifan ikki marta ishga tushirsa,
            # qoldiqni jimgina ikki barobar qilish o'rniga XATO beradi.
            models.UniqueConstraint(
                fields=["store", "product"],
                name="uniq_product_batch_store_product",
            ),
        ]

    def __str__(self):
        return f'Batches {self.store.name} {self.product.name}'


class ProductLocation(TimestampMixin):
    location = models.TextField()
    description = models.TextField()

    class Meta:
        db_table = 'product_location'

    def __str__(self):
        return f"{self.location} location"


class ProductUnitMeasurement(TimestampMixin):
    measurement = models.CharField(max_length=50)

    class Meta:
        db_table = 'product_unit_measurement'

    def __str__(self):
        return self.measurement