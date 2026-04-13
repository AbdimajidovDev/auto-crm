from django.contrib import admin

from apps.transfer.models import StockTransferItem


# Register your models here.


@admin.register(StockTransferItem)
class StockTransferItemAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'stock_transfer', 'product'
    )
