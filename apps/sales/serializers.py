from rest_framework import serializers
from .models import Sale, SaleItem, Payment


class SaleItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleItem
        fields = (
            'id', 'product', 'quantity', 'unit_price', 'total_price'
        )

class SaleListSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True)
    class Meta:
        model = Sale
        fields = (
            'id', 'store', 'seller', 'customer', 'payments',
            'status', 'total_amount', 'paid_amount',
            'items', 'created_at'
        )





class SaleItemInputSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    quantity = serializers.IntegerField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2)


class PaymentInputSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=Payment.Type.choices)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)


class SaleCreateSerializer(serializers.Serializer):
    store = serializers.IntegerField(required=False)
    customer = serializers.IntegerField(required=False, allow_null=True)

    items = SaleItemInputSerializer(many=True)
    payments = PaymentInputSerializer(many=True)

    def validate(self, data):
        if not data["items"]:
            raise serializers.ValidationError("Items bo‘sh bo‘lmasligi kerak")

        if not data["payments"]:
            raise serializers.ValidationError("Payment bo‘sh bo‘lmasligi kerak")

        return data