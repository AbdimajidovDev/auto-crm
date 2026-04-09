from django.contrib import admin
from modeltranslation.admin import TranslationAdmin

from apps.store.models import Store, StoreUser


# Register your models here.


@admin.register(Store)
class StoreAdmin(TranslationAdmin):
    list_display = ('id', 'name', 'phone_number', "type", "address", "latitude", "longitude")
    search_fields = ('name_uz', 'name_uz_cyrl', "phone_number")
    list_filter = ('type',)


@admin.register(StoreUser)
class StoreUserAdmin(admin.ModelAdmin):
    list_display = ('id', "role", "user", "store", "is_active", "created_at")
    list_filter = ('is_active', "role")
    search_fields = ('user__full_name', 'user__email', 'user__phone_number', 'store__name')
