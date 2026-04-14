from rest_framework import serializers
from decimal import Decimal

from apps.sales.models import Payment


class PayDebtListSerializer(serializers.ModelSerializer):
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = (
            'id', 'sale', 'customer', 'customer_name', 'amount', 'type', 'created_at',
        )

    def get_customer_name(self, obj):
        return obj.customer.full_name if obj.customer else ''


class PayDebtSerializer(serializers.Serializer):
    sale = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    type = serializers.ChoiceField(choices=Payment.Type.choices)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be positive")
        return value