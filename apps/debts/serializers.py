from rest_framework import serializers
from decimal import Decimal

from apps.sales.models import Payment


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


class PayDebtSerializer(serializers.Serializer):
    sale = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    type = serializers.ChoiceField(choices=Payment.Type.choices)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be positive")
        return value


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 5 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [PayDebtListSerializer modelini view querysetiga moslash]
# ═══════════════════════════════
