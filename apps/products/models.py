from decimal import Decimal
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
    min_stock = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Juft/dona xususiyati endi unit_measurement (o'lchov birligi) orqali boshqariladi.
    # is_pair maydoni esa backward-compatibility uchun sinxron holda saqlanadi.
    is_pair = models.BooleanField(default=False)

    @property
    def is_pair_effective(self) -> bool:
        if self.unit_measurement_id and self.unit_measurement:
            return self.unit_measurement.quantity_type == ProductUnitMeasurement.QuantityType.QUARTER
        return self.is_pair

    @property
    def quantity_step(self) -> Decimal:
        return Decimal("0.25") if self.is_pair_effective else Decimal("1")

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
        if self.unit_measurement_id and self.unit_measurement:
            self.is_pair = (self.unit_measurement.quantity_type == ProductUnitMeasurement.QuantityType.QUARTER)
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
    min_stock = models.DecimalField(max_digits=12, decimal_places=2, default=0)
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
    class QuantityType(models.TextChoices):
        WHOLE = "WHOLE", "Dona"
        QUARTER = "QUARTER", "Juft"

    measurement = models.CharField(max_length=50)
    quantity_type = models.CharField(
        max_length=20,
        choices=QuantityType.choices,
        default=QuantityType.WHOLE,
        db_index=True,
    )

    class Meta:
        db_table = 'product_unit_measurement'

    def __str__(self):
        return self.measurement

    @property
    def step(self) -> Decimal:
        return Decimal("0.25") if self.quantity_type == self.QuantityType.QUARTER else Decimal("1")

    @property
    def is_pair(self) -> bool:
        return self.quantity_type == self.QuantityType.QUARTER


class ProductFieldHistory(TimestampMixin):
    """Mahsulot master-data ma'lumotlari (nomi, narxi, birligi, SKU, barcode, min_stock va h.k.) o'zgarishlari tarixi."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="field_histories")
    field_name = models.CharField(max_length=64, db_index=True)
    field_label = models.CharField(max_length=128)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    user = models.ForeignKey("users.User", on_delete=models.SET_NULL, null=True, blank=True)
    user_display = models.CharField(max_length=160, blank=True, default="")

    class Meta:
        db_table = "product_field_history"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["product", "created_at"]),
        ]

    def __str__(self):
        return f"{self.product.name} — {self.field_label}: {self.old_value} -> {self.new_value}"