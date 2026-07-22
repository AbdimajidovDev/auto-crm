from decimal import Decimal

from rest_framework import serializers

from apps.contract.models import Supplier, SupplierTransaction
from apps.contract.models import StockEntry
from apps.contract.serializers.stock_entry_serializer import validate_purchase_bank_card_scope
from apps.sales.models import BankCard


class SupplierPaymentListSerializer(serializers.ModelSerializer):
    bank_card_name = serializers.CharField(source="bank_card.name", read_only=True, default=None)

    class Meta:
        model = SupplierTransaction
        fields = (
            'id', 'supplier', 'entry', 'amount', 'type',
            'payment_method', 'bank_card', 'bank_card_name', 'payment_group', 'note', 'created_at',
        )

class SupplierPaymentSplitInputSerializer(serializers.Serializer):
    """
    Bitta split to'lov qatori — sotuvdagi payments massiv elementlari bilan
    bir xil shakl: {type: cash|card, amount, bank_card?}.
    """
    type = serializers.ChoiceField(choices=SupplierTransaction.PaymentMethod.choices)
    amount = serializers.DecimalField(max_digits=15, decimal_places=2)
    bank_card = serializers.PrimaryKeyRelatedField(
        queryset=BankCard.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )

    def validate(self, data):
        if data["amount"] <= 0:
            raise serializers.ValidationError("To'lov miqdori noldan katta bo'lishi kerak")
        bank_card = data.get("bank_card")
        if data["type"] == SupplierTransaction.PaymentMethod.CARD:
            if bank_card is None:
                raise serializers.ValidationError(
                    {"bank_card": "Karta to'lovi uchun to'lov turini (kartani) tanlang"}
                )
            validate_purchase_bank_card_scope(bank_card)
        elif bank_card is not None:
            raise serializers.ValidationError(
                {"bank_card": "Naqd to'lovda bank_card yuborilmasligi kerak"}
            )
        return data


class SupplierPaymentSerializer(serializers.Serializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.filter(is_active=True))
    entry = serializers.PrimaryKeyRelatedField(queryset=StockEntry.objects.select_related("supplier"))
    # Split rejimda amount ixtiyoriy — payments yig'indisidan olinadi
    amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, required=False, allow_null=True, default=None
    )
    note = serializers.CharField(required=False, allow_blank=True)
    # Yangi klientlar: bir nechta usul bilan to'lash — har usul alohida qator
    payments = SupplierPaymentSplitInputSerializer(many=True, required=False)
    # Eski (bitta usulli) rejim: naqd (default) yoki karta; karta bo'lsa bank_card majburiy
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

        payments = data.get("payments")
        if payments:
            total = sum((p["amount"] for p in payments), Decimal("0"))
            if data.get("amount") is not None and data["amount"] != total:
                raise serializers.ValidationError(
                    {"amount": "amount split to'lovlar yig'indisiga teng bo'lishi kerak"}
                )
            data["amount"] = total
            card_ids = [
                p["bank_card"].id for p in payments
                if p["type"] == SupplierTransaction.PaymentMethod.CARD
            ]
            if len(card_ids) != len(set(card_ids)):
                raise serializers.ValidationError(
                    {"payments": "Bitta karta bir necha marta tanlangan"}
                )
            if sum(1 for p in payments if p["type"] == SupplierTransaction.PaymentMethod.CASH) > 1:
                raise serializers.ValidationError(
                    {"payments": "Naqd to'lov faqat bitta qator bo'lishi mumkin"}
                )
        else:
            if data.get("amount") is None:
                raise serializers.ValidationError({"amount": "To'lov miqdorini kiriting"})
            if data.get("payment_type") == SupplierTransaction.PaymentMethod.CARD and not data.get("bank_card"):
                raise serializers.ValidationError(
                    {"bank_card": "Karta to'lovi uchun to'lov turini (kartani) tanlang"}
                )
            validate_purchase_bank_card_scope(data.get("bank_card"))

        if data["amount"] <= 0:
            raise serializers.ValidationError(
                {"amount": "To'lov miqdori noldan katta bo'lishi kerak."}
            )
        return data
