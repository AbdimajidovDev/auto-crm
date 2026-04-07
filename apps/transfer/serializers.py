from rest_framework import serializers
from apps.store.models import Store
from apps.products.models import ProductBatch, Product


from rest_framework import serializers
from apps.store.models import Store
from apps.products.models import ProductBatch
from apps.transfer.models import StockTransfer


class TransferListSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockTransfer
        fields = (
            'id', 'from_store', 'to_store', 'quantity', 'purchase_price', 'selling_price',
            'status', 'created_by', 'approved_by', 'approved_at'
        )



class TransferCreateSerializer(serializers.Serializer):
    from_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    to_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))
    quantity = serializers.IntegerField()

    def validate(self, data):
        if data["from_store"] == data["to_store"]:
            raise serializers.ValidationError("Bir xil store bo‘lishi mumkin emas")

        if data["quantity"] <= 0:
            raise serializers.ValidationError("Quantity > 0 bo‘lishi kerak")

        # 🔥 batch borligini tekshiramiz
        batch = ProductBatch.objects.filter(
            store=data["from_store"],
            product_id=data["product"]
        ).first()

        if not batch:
            raise serializers.ValidationError("Product storeda mavjud emas")

        if batch.quantity < data["quantity"]:
            raise serializers.ValidationError({
                "detail": "Yetarli stock yo‘q",
                "available": batch.quantity
            })

        return data
