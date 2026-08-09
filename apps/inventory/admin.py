from django.contrib import admin

from apps.inventory.models import (
    InventorySession,
    InventoryCount,
    InventorySnapshot,
    InventoryMovement,
    InventoryAdjustment,
    LowStockItem,
    StockAdjustment,
)


# Register your models here.


@admin.register(InventorySession)
class InventorySessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'store', 'started_by', 'status', 'snapshot_taken')
    list_filter = ('store', 'started_by', 'status', 'snapshot_taken')


@admin.register(InventorySnapshot)
class InventorySnapshotAdmin(admin.ModelAdmin):
    list_display = ('id', 'session', 'product_id', 'product', 'store', 'expected_quantity')
    list_filter = ('store',)


@admin.register(InventoryCount)
class InventoryCountAdmin(admin.ModelAdmin):
    list_display = ('id', 'session', 'product', 'counted_quantity', 'is_check')
    list_filter = ('session',)


@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    list_display = ('id', 'session', 'product', 'quantity', 'ref_id', 'type')
    list_filter = ('type',)


@admin.register(InventoryAdjustment)
class InventoryAdjustmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'session', 'product', 'difference')
    list_filter = ('session',)


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'store', 'product', 'old_quantity', 'new_quantity',
        'difference', 'reason', 'created_by', 'created_at',
    )
    list_filter = ('reason', 'store')
    list_select_related = ('store', 'product', 'created_by')
    search_fields = ('product__name', 'store__name')


@admin.register(LowStockItem)
class LowStockItemAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'store', 'product', 'current_quantity', 'min_stock',
        'action_type', 'status', 'resolved_at', 'created_at',
    )
    list_filter = ('status', 'action_type', 'store')
    list_select_related = ('store', 'product')
    search_fields = ('product__name', 'store__name')
