from apps.products.models import Product
from apps.users.models.customers import Customer
from apps.sales.models import Sale, SaleItem

from rest_framework import serializers
from django.db.models import Sum, Case, When, F, DecimalField # DecimalField ni import qiling
from django.db import models



class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product  # Mahsulot modelingiz
        fields = ('id', 'name', 'price')


class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = SaleItem
        fields = ('product_name', 'quantity', 'unit_price', 'total_price')


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    store_name = serializers.CharField(source='store.name', read_only=True)

    class Meta:
        model = Sale
        fields = ('id', 'store_name', 'total_amount', 'paid_amount', 'status', 'created_at', 'items')


class CustomerSerializer(serializers.ModelSerializer):
    total_debt = serializers.SerializerMethodField()
    store_debts = serializers.SerializerMethodField()
    sales = SaleSerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = ('id', 'full_name', 'phone_number', 'total_debt', 'store_debts', 'sales')

    def get_total_debt(self, obj):
        debts = obj.debts.aggregate(
            total=Sum(
                Case(
                    When(type='i', then=F('amount')),
                    When(type='d', then=-F('amount')),
                    default=0,
                    output_field=models.DecimalField()
                )
            )
        )
        return debts['total'] or 0

    def get_store_debts(self, obj):
        debts = obj.debts.values('sale__store__name').annotate(
            store_debt=Sum(
                Case(
                    When(type='i', then=F('amount')),
                    When(type='d', then=-F('amount')),
                    default=0,
                    output_field=models.DecimalField()
                )
            )
        ).order_by('sale__store__name')

        return [
            {
                "store": item['sale__store__name'],
                "debt": item['store_debt']
            }
            for item in debts if item['sale__store__name']
        ]
