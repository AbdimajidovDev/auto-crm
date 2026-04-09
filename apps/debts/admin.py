from django.contrib import admin

from apps.debts.models import CustomerDebt


# Register your models here.


@admin.register(CustomerDebt)
class CustomerDebtAdmin(admin.ModelAdmin):
    list_display = ('customer', 'sale', 'amount')

