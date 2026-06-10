from django.contrib import admin

from apps.sales.models import Sale, SaleItem, SaleReturnItem


# Register your models here.

class SaleItemInline(admin.StackedInline):
    model = SaleItem
    extra = 0


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'store', 'customer', 'seller', 'status',
        'total_amount', 'paid_amount', 'created_at'
    )
    inlines = [SaleItemInline]


@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'sale')


@admin.register(SaleReturnItem)
class SaleReturnAdmin(admin.ModelAdmin):
    list_display = ('id', 'sale_return')
