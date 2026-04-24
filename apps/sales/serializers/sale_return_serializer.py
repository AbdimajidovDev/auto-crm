from rest_framework import serializers

from apps.sales.models import SaleReturn, SaleReturnItem


class SaleReturnItemInputSerializer(serializers.Serializer):
    sale_item = serializers.IntegerField()
    quantity = serializers.IntegerField()


class SaleReturnCreateSerializer(serializers.Serializer):
    sale = serializers.IntegerField()
    items = SaleReturnItemInputSerializer(many=True)

    comment = serializers.CharField(required=False, allow_blank=True)



class SaleReturnItemSerializer(serializers.ModelSerializer):
    product_name = serializers.SerializerMethodField()

    class Meta:
        model = SaleReturnItem
        fields = ('id', 'sale_item', 'product', 'product_name', 'quantity')

    def get_product_name(self, obj):
        return obj.product.name if obj.product else ''


class SaleReturnListSerializer(serializers.ModelSerializer):
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
