from django.contrib import admin
from modeltranslation.admin import TranslationAdmin

from apps.contract.models import Supplier, StockEntry, StockEntryItem


# Register your models here.


@admin.register(Supplier)
class SupplierAdmin(TranslationAdmin):
    list_display = ('id', 'name', 'phone_number', "inn", "address", "is_active")
    list_filter = ('is_active',)
    search_fields = ('name', "phone_number")


@admin.register(StockEntry)
class StockEntryAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'store', 'created_at')
    list_filter = ('supplier',)
    search_fields = ('supplier__name',)


@admin.register(StockEntryItem)
class StockEntryItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'entry', 'product', 'quantity')
    list_filter = ('entry',)
    search_fields = ("product__name",)
