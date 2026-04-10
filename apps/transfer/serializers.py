from apps.products.models import Product


from rest_framework import serializers
from apps.store.models import Store
from apps.transfer.models import StockTransfer


class TransferItemSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))
    quantity = serializers.IntegerField(min_value=1)

    def validate(self, data):
        print('data', data)

        # # 🔥 batch borligini tekshiramiz
        # batch = ProductBatch.objects.filter(
        #     store=data["from_store"],
        #     product_id=data["product"]
        # ).first()
        #
        # if not batch:
        #     raise serializers.ValidationError("Product storeda mavjud emas")
        #
        # if batch.quantity < data["quantity"]:
        #     raise serializers.ValidationError({
        #         "detail": "Yetarli stock yo‘q",
        #         "available": batch.quantity
        #     })
        return data


class TransferListSerializer(serializers.ModelSerializer):
    items = TransferItemSerializer(many=True)
    class Meta:
        model = StockTransfer
        fields = (
            'id', 'from_store', 'to_store', 'status', 'created_by',
            'approved_by', 'approved_at', 'items'
        )


class TransferCreateSerializer(serializers.Serializer):
    from_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    to_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    items = TransferItemSerializer(many=True)  # Bir nechta mahsulot

    def validate(self, data):
        if data["from_store"] == data["to_store"]:
            raise serializers.ValidationError("Do'konlar bir xil bo'lmasligi kerak")

        if not data.get("items"):
            raise serializers.ValidationError("Kamida bitta mahsulot bo'lishi shart")

        return data
