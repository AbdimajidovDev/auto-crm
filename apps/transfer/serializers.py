from apps.products.models import Product


from rest_framework import serializers
from apps.store.models import Store
from apps.transfer.models import (
    StockTransfer,
    StockTransferItem,
    Notification,
    TransferSession,
)


class TransferSessionSerializer(serializers.ModelSerializer):
    """O'tkazma qoralamasi — avto-saqlash uchun yengil serializer."""

    from_store = serializers.PrimaryKeyRelatedField(
        queryset=Store.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )
    to_store = serializers.PrimaryKeyRelatedField(
        queryset=Store.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )

    # Ro'yxat sahifasida qoralama kartasi uchun do'kon nomlari
    from_store_name = serializers.CharField(source="from_store.name", read_only=True, default=None)
    to_store_name = serializers.CharField(source="to_store.name", read_only=True, default=None)

    class Meta:
        model = TransferSession
        fields = (
            "id", "from_store", "from_store_name", "to_store", "to_store_name",
            "items", "status", "created_at", "updated_at",
        )
        read_only_fields = ("id", "status", "created_at", "updated_at")

    def validate_items(self, items):
        # Qoralama — yengil tekshiruv: list ichida {product, quantity} bo'lishi kifoya.
        # Qat'iy validatsiya (ombor qoldig'i) haqiqiy o'tkazma yaratishda bajariladi.
        if not isinstance(items, list):
            raise serializers.ValidationError("items ro'yxat bo'lishi kerak")
        cleaned = []
        for item in items:
            if not isinstance(item, dict):
                continue
            cleaned.append({
                "product": item.get("product"),
                "quantity": item.get("quantity") or 0,
            })
        return cleaned


class TransferItemSerializer(serializers.ModelSerializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(status=Product.ProductStatus.ACTIVE))
    quantity = serializers.IntegerField(min_value=1)
    product_name = serializers.CharField(source="product.name", read_only=True)
    # product_name = serializers.SerializerMethodField()
    sku = serializers.CharField(source="product.sku", read_only=True)

    class Meta:
        model = StockTransferItem
        fields = (
            'id', 'product', 'product_name', 'sku', 'quantity', 'purchase_price', 'selling_price',
        )
        read_only_fields = ('id','product_name', 'purchase_price', 'selling_price',)

    # def get_product_name(self, obj):
    #     return obj.product.name if obj.product else ""


class TransferListSerializer(serializers.ModelSerializer):
    items = TransferItemSerializer(many=True)
    from_store_name = serializers.CharField(source="from_store.name", read_only=True)
    to_store_name = serializers.CharField(source="to_store.name", read_only=True)
    approved_by_name = serializers.CharField(source="approved_by.full_name", read_only=True)
    # from_store_name = serializers.SerializerMethodField()
    # to_store_name = serializers.SerializerMethodField()
    # approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockTransfer
        fields = (
            'id', 'from_store', 'from_store_name', 'to_store', 'to_store_name',
            'status', 'created_by', 'approved_by', 'approved_by_name', 'approved_at', 'items'
        )

    # def get_from_store_name(self, obj):
    #     return obj.from_store.name if obj.from_store else ""
    #
    # def get_to_store_name(self, obj):
    #     return obj.to_store.name if obj.to_store else ""
    #
    # def get_approved_by_name(self, obj):
    #     return obj.approved_by.full_name if obj.approved_by else ""


class TransferCreateSerializer(serializers.Serializer):
    from_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.filter(is_active=True))
    to_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.filter(is_active=True))
    # from_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    # to_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    items = TransferItemSerializer(many=True)  # Bir nechta mahsulot

    def validate(self, data):
        if data["from_store"] == data["to_store"]:
            raise serializers.ValidationError("Do'konlar bir xil bo'lmasligi kerak")

        if not data.get("items"):
            raise serializers.ValidationError("Kamida bitta mahsulot bo'lishi shart")

        return data


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            'id', 'user', 'type', 'title', 'message', 'is_read', 'transfer'
        )


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 2
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [SerializerMethodField o'rniga `source` ishlatish va view querysetini select_related/prefetch_related bilan moslash]
# ═══════════════════════════════
