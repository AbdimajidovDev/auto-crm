from decimal import Decimal
from rest_framework import serializers

from apps.common.quantity import QuantityField
from apps.inventory.models import StockAdjustment


class StockAdjustmentCreateSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    quantity = QuantityField(required=False, min_value=Decimal("0.01"))
    new_quantity = QuantityField(required=False, min_value=Decimal("0"))
    type = serializers.ChoiceField(
        choices=StockAdjustment.Type.choices,
        default=StockAdjustment.Type.IMPORT,
    )
    reason = serializers.ChoiceField(
        choices=StockAdjustment.Reason.choices,
        required=False,
    )
    comment = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=2000
    )

    def validate(self, attrs):
        if "quantity" not in attrs and "new_quantity" not in attrs:
            raise serializers.ValidationError(
                "Miqdor ('quantity') yoki yangi qoldiq ('new_quantity') kiritilishi shart."
            )
        return attrs


class StockAdjustmentListSerializer(serializers.ModelSerializer):
    # View querysetida select_related("store", "product", "product__unit_measurement", "created_by", "cancelled_by") shart
    store_name = serializers.CharField(source="store.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    sku = serializers.CharField(source="product.sku", read_only=True)
    barcode = serializers.CharField(source="product.barcode", read_only=True)
    unit_name = serializers.CharField(source="product.unit_measurement.measurement", read_only=True, default="")
    unit_step = serializers.DecimalField(source="product.quantity_step", max_digits=5, decimal_places=2, read_only=True)
    unit_is_pair = serializers.BooleanField(source="product.is_pair_effective", read_only=True)

    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=False)
    old_quantity = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=False)
    new_quantity = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=False)
    difference = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=False)
    purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=False)
    sale_price = serializers.DecimalField(max_digits=12, decimal_places=2, coerce_to_string=False)
    total_amount = serializers.DecimalField(max_digits=15, decimal_places=2, coerce_to_string=False)

    created_by_name = serializers.SerializerMethodField()
    cancelled_by_name = serializers.SerializerMethodField()

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
            "unit_name",
            "unit_step",
            "unit_is_pair",
            "type",
            "status",
            "quantity",
            "old_quantity",
            "new_quantity",
            "difference",
            "purchase_price",
            "sale_price",
            "total_amount",
            "reason",
            "comment",
            "created_by",
            "created_by_name",
            "created_at",
            "cancelled_by",
            "cancelled_by_name",
            "cancelled_at",
        )

    def get_created_by_name(self, obj):
        user = obj.created_by
        if user is None:
            return ""
        return user.full_name or user.phone_number

    def get_cancelled_by_name(self, obj):
        user = obj.cancelled_by
        if user is None:
            return ""
        return user.full_name or user.phone_number

