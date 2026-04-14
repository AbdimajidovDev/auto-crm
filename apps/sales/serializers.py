from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from .models import Sale, SaleItem, Payment


class SaleItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleItem
        fields = (
            'id', 'product', 'quantity', 'unit_price', 'total_price'
        )

class SaleListSerializer(serializers.ModelSerializer):
    store_name = serializers.SerializerMethodField()
    customer_name = serializers.SerializerMethodField()
    seller_name = serializers.SerializerMethodField()
    debt = serializers.SerializerMethodField()
    items = SaleItemSerializer(many=True)

    class Meta:
        model = Sale
        fields = (
            'id', 'store', 'store_name', 'seller', 'seller_name', 'customer', 'customer_name',
            'payments', 'status', 'total_amount', 'paid_amount', 'debt',
            'discount_type', 'discount_value', 'discount_amount', 'items', 'created_at',
        )

    def get_store_name(self, obj):
        return obj.store.name if obj.store else None

    def get_customer_name(self, obj):
        return obj.customer.full_name if obj.customer else None

    def get_seller_name(self, obj):
        return obj.seller.full_name if obj.seller else None

    def get_debt(self, obj):
        debt = (obj.total_increase or 0) - (obj.total_decrease or 0)
        return debt if debt > 0 else None


class SaleItemInputSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    quantity = serializers.IntegerField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2)

    def validate(self, data):
        quantity = data['quantity']
        price = data['price']

        if quantity <= 0:
            raise ValidationError("Miqdor ijoboy bo'lishi kerak")

        if price <= 0:
            raise ValidationError("Narx ijoboy bo'lishi kerak")
        return data


class PaymentInputSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=Payment.Type.choices)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)

    def validate(self, data):
        amount = data['amount']

        if amount <= 0:
            raise ValidationError("To'lov ijoboy bo'lishi kerak")
        return data



class SaleCreateSerializer(serializers.Serializer):
    store = serializers.IntegerField(required=False)
    customer = serializers.IntegerField(required=False, allow_null=True)

    # Chegirma uchun yangi maydonlar
    discount_type = serializers.ChoiceField(choices=Sale.DiscountType.choices, required=False, allow_null=True)
    discount_value = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)

    items = SaleItemInputSerializer(many=True)
    payments = PaymentInputSerializer(many=True)

    def validate(self, data):

        if not data["items"]:
            raise serializers.ValidationError("Items bo‘sh bo‘lmasligi kerak")

        if not data["payments"]:
            raise serializers.ValidationError("Payment bo‘sh bo‘lmasligi kerak")

        # Foizli chegirma 100% dan oshmasligi kerak
        if data.get("discount_type") == Sale.DiscountType.PERCENTAGE:
            if data.get("discount_value", 0) > 100:
                raise serializers.ValidationError("Chegirma foizi 100 dan oshmasligi kerak")

        return data



# ------------------------------------------------------------------------------

from rest_framework import serializers


class CustomerDebtListSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    customer_id = serializers.IntegerField()
    customer_name = serializers.CharField()
    total_debt = serializers.DecimalField(max_digits=20, decimal_places=2)
