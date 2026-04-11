from rest_framework import serializers

from apps.contract.models import Supplier, SupplierTransaction


class SupplierPaymentListSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupplierTransaction
        fields = (
            'id', 'supplier', 'entry', 'amount', 'type', 'note'
        )

class SupplierPaymentSerializer(serializers.Serializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.all())
    amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0.01)
    note = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("To'lov miqdori noldan katta bo'lishi kerak.")
        return value