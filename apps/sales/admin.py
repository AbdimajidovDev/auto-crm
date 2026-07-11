from django.contrib import admin

from apps.sales.models import BankCard, Payment, Sale, SaleItem, SaleReturnItem, SaleReturn


# Register your models here.

class SaleItemInline(admin.StackedInline):
    model = SaleItem
    extra = 0


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'store', 'customer', 'seller', 'status', 'payment_type',
        'total_amount', 'paid_amount', 'created_at'
    )
    list_filter = ('payment_type', 'status')
    inlines = [SaleItemInline]


@admin.register(BankCard)
class BankCardAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'is_default', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'sale', 'customer', 'amount', 'type', 'bank_card', 'is_refund', 'created_at')
    list_filter = ('type', 'is_refund', 'bank_card')
    list_select_related = ('sale', 'customer', 'bank_card')


@admin.register(SaleReturn)
class SaleReturnAdmin(admin.ModelAdmin):
    list_display = ('id', 'sale')
