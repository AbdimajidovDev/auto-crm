from rest_framework import serializers

from apps.sales.models import SaleReturn, SaleReturnItem
from apps.sales.serializers.sale_serializer import PaymentInputSerializer


class SaleReturnItemInputSerializer(serializers.Serializer):
    sale_item = serializers.IntegerField()
    quantity = serializers.IntegerField()


class SaleReturnCreateSerializer(serializers.Serializer):
    sale = serializers.IntegerField()
    items = SaleReturnItemInputSerializer(many=True)

    # Mijozga pul QANDAY qaytarilishi — sotuvdagi kabi erkin taqsim:
    # masalan 50% naqd + 50% karta. Yuborilmasa — eski xatti-harakat (hammasi naqd).
    payments = PaymentInputSerializer(many=True, required=False)

    comment = serializers.CharField(required=False, allow_blank=True)



class SaleReturnItemSerializer(serializers.ModelSerializer):
    # ⚠️ MUAMMO [PERFORMANCE]: `SerializerMethodField` product FK ga murojaat qiladi.
    # Sabab: view querysetida `items__product` prefetch qilinmasa har return item uchun query chiqadi.
    # Natija: qaytarishlar ro'yxatida N+1 muammosi paydo bo'ladi.
    # ✅ YECHIM:
    # product_name = serializers.CharField(source="product.name", read_only=True)
    product_name = serializers.SerializerMethodField()

    class Meta:
        model = SaleReturnItem
        fields = ('id', 'sale_item', 'product', 'product_name', 'quantity')

    def get_product_name(self, obj):
        # N+1: `SaleReturnListAPIView` querysetida `items` va `product` prefetch bo'lmasa har bir qator uchun so'rov.
        return obj.product.name if obj.product else ''


class SaleReturnListSerializer(serializers.ModelSerializer):
    # ⚠️ MUAMMO [PERFORMANCE]: `store_name` va `seller_name` SerializerMethodField orqali FK o'qiydi.
    # Sabab: viewda `select_related("store", "seller")` bo'lmasa har obyekt uchun qo'shimcha query ishlaydi.
    # Natija: list endpoint katta bo'lganda latency oshadi.
    # ✅ YECHIM:
    # store_name = serializers.CharField(source="store.name", read_only=True)
    # seller_name = serializers.CharField(source="seller.full_name", read_only=True)
    # N+1: `store`, `seller` FK nomlari serializer method orqali — viewda `select_related("store", "seller")` kerak.
    store_name = serializers.SerializerMethodField()
    seller_name = serializers.SerializerMethodField()

    items = SaleReturnItemSerializer(many=True)

    class Meta:
        model = SaleReturn
        fields = (
            'id', 'sale', 'store',  'store_name', 'customer', 'seller', 'seller_name', 'total_refund', 'comment', 'items'
        )

    def get_store_name(self, obj):
        return obj.store.name if obj.store else ''

    def get_seller_name(self, obj):
        return obj.seller.full_name if obj.seller else ''


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 2
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [SerializerMethodFieldlarni `source` fieldlarga almashtirish va view querysetini moslash]
# ═══════════════════════════════
