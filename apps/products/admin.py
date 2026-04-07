from django.contrib import admin

from apps.products.models import Product, ProductImage, ProductBatch


# Register your models here.


class ProductImageInline(admin.StackedInline):
    model = ProductImage
    extra = 0


@admin.register(Product)
class ProductListAdmin(admin.ModelAdmin):
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
    list_display = ('id', 'product', 'store', 'quantity', 'purchase_price', 'selling_price', 'created_at')
    list_filter = ('product',)
