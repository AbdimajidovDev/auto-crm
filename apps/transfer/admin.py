from django.contrib import admin

from apps.transfer.models import StockTransferItem, StockTransfer, Notification


# Register your models here.


@admin.register(StockTransferItem)
class StockTransferItemAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'stock_transfer', 'product'
    )


@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    list_display = ('id', 'from_store', 'to_store', 'status')


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'type', 'title', 'created_at', 'updated_at')
