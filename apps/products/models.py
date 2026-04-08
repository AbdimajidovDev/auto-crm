from django.db import models
from django.utils.text import slugify

from apps.common.models.timestamp_mixin import TimestampMixin
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


class Product(TimestampMixin):
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    name = models.CharField(max_length=100)
    description = models.TextField()
    is_active = models.BooleanField(default=True)


    def __str__(self):
        return self.name

    class Meta:
        db_table = 'product'
        # ordering = ['name']


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to=product_image_path)

    def __str__(self):
        return self.product.name

    class Meta:
        db_table = 'product_image'


class ProductBatch(TimestampMixin):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='batches')
    store = models.ForeignKey(Store, on_delete=models.CASCADE)

    quantity = models.IntegerField()

    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)

    barcode = models.CharField(max_length=12, db_index=True, unique=True)
    shtrix_code = models.ImageField(upload_to=product_barcode_path)


    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "product_batch"
        unique_together = ("store", "barcode")
        indexes = [
            models.Index(fields=["store", "product"]),
        ]
