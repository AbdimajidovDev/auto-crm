from rest_framework import serializers

from apps.contract.models import Supplier, SupplierTransaction
from apps.contract.models import StockEntry


class SupplierPaymentListSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupplierTransaction
        fields = (
            'id', 'supplier', 'entry', 'amount', 'type', 'note'
        )

class SupplierPaymentSerializer(serializers.Serializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.filter(is_active=True))
    entry = serializers.PrimaryKeyRelatedField(queryset=StockEntry.objects.select_related("supplier"))
    amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0.01)
    note = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        if data["entry"].supplier_id != data["supplier"].id:
            raise serializers.ValidationError("Entry supplierga tegishli emas")
        return data

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("To'lov miqdori noldan katta bo'lishi kerak.")
        return value
