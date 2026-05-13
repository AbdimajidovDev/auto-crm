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
    # ⚠️ MUAMMO [KRITIK/PERFORMANCE]: `.all()` querysetlar inactive yoki begona entrylarni cheklamaydi.
    # Sabab: supplier va entry orasidagi moslik serializer field darajasida ham, validate()da ham tekshirilmagan.
    # Natija: boshqa supplier entrysiga payment yozish yoki katta jadvalda ortiqcha lookup xavfi bor.
    # ✅ YECHIM:
    # supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.filter(is_active=True))
    # entry = serializers.PrimaryKeyRelatedField(queryset=StockEntry.objects.select_related("supplier"))
    # def validate(self, data):
    #     if data["entry"].supplier_id != data["supplier"].id:
    #         raise serializers.ValidationError("Entry supplierga tegishli emas")
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.all())
    entry = serializers.PrimaryKeyRelatedField(queryset=StockEntry.objects.all())
    amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0.01)
    note = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("To'lov miqdori noldan katta bo'lishi kerak.")
        return value


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [SupplierPaymentSerializer.validate orqali supplier-entry mosligini tekshirish]
# ═══════════════════════════════
