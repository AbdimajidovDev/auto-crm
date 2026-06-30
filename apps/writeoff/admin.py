from django.contrib import admin

from apps.writeoff.models import WriteOff, WriteOffItem


class WriteOffItemInline(admin.TabularInline):
    model = WriteOffItem
    extra = 0
    raw_id_fields = ("product",)


@admin.register(WriteOff)
class WriteOffAdmin(admin.ModelAdmin):
    list_display = ("id", "store", "reason", "total_amount", "created_by", "created_at")
    list_filter = ("reason", "store")
    search_fields = ("comment",)
    raw_id_fields = ("store", "created_by", "inventory_session")
    inlines = (WriteOffItemInline,)
