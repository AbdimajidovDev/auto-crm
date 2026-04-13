from apps.products.models import Product


from rest_framework import serializers
from apps.store.models import Store
from apps.transfer.models import StockTransfer, StockTransferItem, Notification


class TransferItemSerializer(serializers.ModelSerializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))
    quantity = serializers.IntegerField(min_value=1)
    product_name = serializers.SerializerMethodField()

    class Meta:
        model = StockTransferItem
        fields = (
            'id', 'product', 'product_name', 'quantity', 'purchase_price', 'selling_price',
        )
        read_only_fields = ('id','product_name', 'purchase_price', 'selling_price',)

    def get_product_name(self, obj):
        return obj.product.name if obj.product else ""

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
    from_store_name = serializers.SerializerMethodField()
    to_store_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockTransfer
        fields = (
            'id', 'from_store', 'from_store_name', 'to_store', 'to_store_name',
            'status', 'created_by', 'approved_by', 'approved_by_name', 'approved_at', 'items'
        )

    def get_from_store_name(self, obj):
        return obj.from_store.name if obj.from_store else ""

    def get_to_store_name(self, obj):
        return obj.to_store.name if obj.to_store else ""

    def get_approved_by_name(self, obj):
        return obj.approved_by.full_name if obj.approved_by else ""


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


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            'id', 'user', 'type', 'title', 'message', 'is_read', 'transfer'
        )