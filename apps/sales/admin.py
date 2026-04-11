from django.contrib import admin

from apps.sales.models import Sale


# Register your models here.


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'store', 'customer', 'seller', 'status',
        'total_amount', 'paid_amount', 'created_at'
    )
