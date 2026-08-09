from rest_framework import serializers

from apps.inventory.models import StockAdjustment


class StockAdjustmentCreateSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    # Decimal: juft mahsulot qoldig'i kasr (0.5 qadam) bo'lishi mumkin
    new_quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0
    )
    reason = serializers.ChoiceField(
        choices=StockAdjustment.Reason.choices,
        default=StockAdjustment.Reason.RECOUNT,
    )
    comment = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=2000
    )


class StockAdjustmentListSerializer(serializers.ModelSerializer):
    # View querysetida select_related("store", "product", "created_by") shart —
    # aks holda har qator uchun alohida so'rov chiqadi (N+1).
    store_name = serializers.CharField(source="store.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    sku = serializers.CharField(source="product.sku", read_only=True)
    barcode = serializers.CharField(source="product.barcode", read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockAdjustment
        fields = (
            "id",
            "store",
            "store_name",
            "product",
            "product_name",
            "sku",
            "barcode",
            "old_quantity",
            "new_quantity",
            "difference",
            "reason",
            "comment",
            "created_by",
            "created_by_name",
            "created_at",
        )

    def get_created_by_name(self, obj):
        user = obj.created_by
        if user is None:
            return ""
        return user.full_name or user.phone_number
