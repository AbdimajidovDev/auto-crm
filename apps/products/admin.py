from django.contrib import admin
from modeltranslation.admin import TranslationAdmin

from apps.products.models import Product, ProductImage, ProductBatch, Category


# Register your models here.

@admin.register(Category)
class CategoryAdmin(TranslationAdmin):
    list_display = ('id', 'slug', 'name', 'description')


class ProductImageInline(admin.StackedInline):
    model = ProductImage
    extra = 0


@admin.register(Product)
class ProductListAdmin(TranslationAdmin):
    list_display = ('id', 'name', 'category', 'created_at')
    search_fields = ('name',)
    list_filter = ('category',)

    inlines = [
        ProductImageInline
    ]


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'image')
    list_filter = ('product',)


@admin.register(ProductBatch)
class ProductBatchAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'product__id', 'product', 'store__id', 'store', 'quantity',
        'purchase_price', 'selling_price', 'created_at',
    )
    list_filter = ('product', 'store')
