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
        return self.name

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
            return self.name

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
            if not self.sku:
                self.sku = self.generate_sku()
                update_fields.append("sku")
            if not self.barcode:
                self.barcode = generate_unique_barcode()
                self.shtrix_code.save(
                    f"{self.barcode}.png",
                    generate_barcode_image(self.barcode),
                    save=False
                )
                update_fields.extend(["barcode", "shtrix_code"])
            if update_fields:
                super().save(update_fields=update_fields)

    def __str__(self):
        return self.name

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

    quantity = models.IntegerField()

    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "product_batch"
        indexes = [
            models.Index(fields=["store", "product"]),
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