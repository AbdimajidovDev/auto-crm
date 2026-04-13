from rest_framework import serializers

from apps.products.models import ProductBatch


class ProductBatchSearchSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name")
    category_name = serializers.CharField(source="product.category.name")
    store_name = serializers.CharField(source="store.name")

    class Meta:
        model = ProductBatch
        fields = (
            "id",
            "product",
            "product_name",
            "category_name",
            "store",
            "store_name",
            "quantity",
            "selling_price",
            "barcode",
        )
