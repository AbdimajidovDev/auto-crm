from django.contrib import admin
from modeltranslation.admin import TranslationAdmin

from apps.contract.models import Supplier


# Register your models here.


@admin.register(Supplier)
class SupplierAdmin(TranslationAdmin):
    list_display = ('name', 'phone_number', "inn", "address", "is_active")
    list_filter = ('is_active',)
    search_fields = ('name', "phone_number")
