from rest_framework import serializers
from decimal import Decimal

from apps.sales.models import BankCard, Payment
from apps.sales.services.payment_service import validate_payment_method


class PayDebtListSerializer(serializers.ModelSerializer):
    # ⚠️ MUAMMO [KRITIK]: Serializer `Payment` modeliga yozilgan, ammo view `CustomerDebt` queryset uzatyapti.
    # Sabab: Payment va CustomerDebt maydonlari o'xshash ko'rinsa ham domain ma'nosi boshqa.
    # Natija: `type`, `created_at`, customer ma'lumotlari noto'g'ri talqin qilinadi.
    # ✅ YECHIM:
    # class CustomerDebtListSerializer(serializers.ModelSerializer):
    #     customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    #     class Meta:
    #         model = CustomerDebt
    #         fields = ("id", "sale", "customer", "customer_name", "amount", "type", "created_at")
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = (
            'id', 'sale', 'customer', 'customer_name', 'amount', 'type', 'created_at',
        )

    def get_customer_name(self, obj):
        # ⚠️ MUAMMO [PERFORMANCE]: `SerializerMethodField` customer FKga murojaat qiladi.
        # Sabab: querysetda `select_related("customer")` bo'lmasa har satr uchun query chiqadi.
        # Natija: payment/debt listda N+1 muammosi yuzaga keladi.
        # ✅ YECHIM:
        # customer_name = serializers.CharField(source="customer.full_name", read_only=True)
        # N+1: ro'yxatda `select_related("customer")` bo'lmasa har bir to'lov uchun alohida so'rov.
        return obj.customer.full_name if obj.customer else ''


class DebtPaymentInputSerializer(serializers.Serializer):
    """
    Qarz to'lovining bitta split qatori — sotuv yaratishdagi
    PaymentInputSerializer bilan bir xil shakl: {type, amount, bank_card?}.
    """
    type = serializers.ChoiceField(choices=Payment.Type.choices)
    amount = serializers.DecimalField(max_digits=20, decimal_places=2)
    bank_card = serializers.PrimaryKeyRelatedField(
        queryset=BankCard.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )

    def validate(self, data):
        if data["amount"] <= 0:
            raise serializers.ValidationError("To'lov miqdori noldan katta bo'lishi kerak")
        validate_payment_method(data["type"], data.get("bank_card"))
        return data


def _validate_split_or_single(data):
    """
    payments (split) va eski bitta usulli maydonlarning umumiy validatsiyasi:
      - payments berilsa: amount ixtiyoriy (berilsa yig'indiga teng bo'lishi shart),
        bitta karta takrorlanmasligi, naqd faqat bitta qator.
      - payments bo'lmasa: type + amount majburiy (eski rejim).
    """
    payments = data.get("payments")
    if payments:
        total = sum((p["amount"] for p in payments), Decimal("0"))
        if data.get("amount") is not None and data["amount"] != total:
            raise serializers.ValidationError(
                {"amount": "amount split to'lovlar yig'indisiga teng bo'lishi kerak"}
            )
        data["amount"] = total
        card_ids = [p["bank_card"].id for p in payments if p["type"] == Payment.Type.CARD]
        if len(card_ids) != len(set(card_ids)):
            raise serializers.ValidationError(
                {"payments": "Bitta karta bir necha marta tanlangan"}
            )
        if sum(1 for p in payments if p["type"] == Payment.Type.CASH) > 1:
            raise serializers.ValidationError(
                {"payments": "Naqd to'lov faqat bitta qator bo'lishi mumkin"}
            )
    else:
        if data.get("type") is None:
            raise serializers.ValidationError(
                {"type": "To'lov usulini tanlang yoki payments ro'yxatini yuboring"}
            )
        if data.get("amount") is None:
            raise serializers.ValidationError({"amount": "To'lov miqdorini kiriting"})
        # Sotuvdagi bilan bir xil markaziy qoida: card → bank_card majburiy, cash → taqiqlanadi
        validate_payment_method(data["type"], data.get("bank_card"))

    if data["amount"] <= 0:
        raise serializers.ValidationError({"amount": "To'lov miqdori noldan katta bo'lishi kerak"})
    return data


class PayDebtSerializer(serializers.Serializer):
    sale = serializers.IntegerField()
    # Split rejimda amount ixtiyoriy — payments yig'indisidan olinadi
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True, default=None
    )
    # Yangi rejim: bir nechta usul bilan to'lash (naqd + kartalar)
    payments = DebtPaymentInputSerializer(many=True, required=False)
    # Eski (bitta usulli) rejim
    type = serializers.ChoiceField(choices=Payment.Type.choices, required=False, allow_null=True, default=None)
    bank_card = serializers.PrimaryKeyRelatedField(
        queryset=BankCard.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )

    def validate(self, data):
        return _validate_split_or_single(data)


class CustomerPayDebtSerializer(serializers.Serializer):
    """Mijozning umumiy qarzini FIFO tartibida to'lash (eng eski buyurtmadan boshlab)."""
    customer = serializers.IntegerField()
    # Split rejimda amount ixtiyoriy — payments yig'indisidan olinadi
    amount = serializers.DecimalField(
        max_digits=20, decimal_places=2, required=False, allow_null=True, default=None
    )
    # Yangi rejim: bir nechta usul bilan to'lash (naqd + kartalar)
    payments = DebtPaymentInputSerializer(many=True, required=False)
    # Eski (bitta usulli) rejim
    type = serializers.ChoiceField(choices=Payment.Type.choices, required=False, allow_null=True, default=None)
    bank_card = serializers.PrimaryKeyRelatedField(
        queryset=BankCard.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )

    def validate(self, data):
        return _validate_split_or_single(data)


class CustomerPaymentListSerializer(serializers.ModelSerializer):
    """Mijozning to'lovlar tarixi (qarz to'lovlari ham, sotuv to'lovlari ham)."""
    bank_card_name = serializers.CharField(source="bank_card.name", read_only=True, default="")

    class Meta:
        model = Payment
        fields = (
            "id", "sale", "amount", "type", "bank_card", "bank_card_name",
            "is_refund", "payment_group", "created_at",
        )


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 5 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [PayDebtListSerializer modelini view querysetiga moslash]
# ═══════════════════════════════
