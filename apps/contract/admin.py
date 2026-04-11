from django.contrib import admin
from modeltranslation.admin import TranslationAdmin

from apps.contract.models import Supplier, StockEntry, StockEntryItem, SupplierTransaction


# Register your models here.


@admin.register(Supplier)
class SupplierAdmin(TranslationAdmin):
    list_display = ('id', 'name', 'phone_number', "inn", "address", "is_active")
    list_filter = ('is_active',)
    search_fields = ('name', "phone_number")


class StockEntryItemInline(admin.StackedInline):
    model = StockEntryItem
    extra = 0

@admin.register(StockEntry)
class StockEntryAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'store', 'created_at')
    list_filter = ('supplier',)
    search_fields = ('supplier__name',)

    inlines = (StockEntryItemInline,)


@admin.register(StockEntryItem)
class StockEntryItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'entry', 'product', 'quantity')
    list_filter = ('entry',)
    search_fields = ("product__name",)



@admin.register(SupplierTransaction)
class SupplierTransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'entry', 'amount', 'type', 'created_at')
    list_filter = ('supplier',)