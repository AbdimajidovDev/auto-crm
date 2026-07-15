from rest_framework import serializers

from apps.contract.models import Supplier, SupplierTransaction
from apps.contract.models import StockEntry
from apps.sales.models import BankCard


class SupplierPaymentListSerializer(serializers.ModelSerializer):
    bank_card_name = serializers.CharField(source="bank_card.name", read_only=True, default=None)

    class Meta:
        model = SupplierTransaction
        fields = (
            'id', 'supplier', 'entry', 'amount', 'type',
            'payment_method', 'bank_card', 'bank_card_name', 'note', 'created_at',
        )

class SupplierPaymentSerializer(serializers.Serializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.filter(is_active=True))
    entry = serializers.PrimaryKeyRelatedField(queryset=StockEntry.objects.select_related("supplier"))
    amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0.01)
    note = serializers.CharField(required=False, allow_blank=True)
    # To'lov usuli: naqd (default) yoki karta; karta bo'lsa bank_card majburiy
    payment_type = serializers.ChoiceField(
        choices=SupplierTransaction.PaymentMethod.choices,
        required=False,
        default=SupplierTransaction.PaymentMethod.CASH,
    )
    bank_card = serializers.PrimaryKeyRelatedField(
        queryset=BankCard.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )

    def validate(self, data):
        if data["entry"].supplier_id != data["supplier"].id:
            raise serializers.ValidationError("Entry supplierga tegishli emas")
        if data.get("payment_type") == SupplierTransaction.PaymentMethod.CARD and not data.get("bank_card"):
            raise serializers.ValidationError(
                {"bank_card": "Karta to'lovi uchun to'lov turini (kartani) tanlang"}
            )
        return data

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("To'lov miqdori noldan katta bo'lishi kerak.")
        return value
