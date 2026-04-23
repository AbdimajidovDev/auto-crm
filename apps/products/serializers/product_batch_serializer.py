from rest_framework import serializers

from apps.products.models import ProductBatch, ProductUnitMeasurement, ProductLocation


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
            "location"
        )


class ProductLocationGetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductLocation
        fields = ('id', 'location', 'description')


class ProductLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductLocation
        fields = ('id', 'location_uz', 'location_uz_cyrl', 'description_uz', 'description_uz_cyrl')


class ProductUnitMeasurementGetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductUnitMeasurement
        fields = ('id', 'measurement')

class ProductUnitMeasurementSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductUnitMeasurement
        fields = ('id', 'measurement_uz', 'measurement_uz_cyrl')
