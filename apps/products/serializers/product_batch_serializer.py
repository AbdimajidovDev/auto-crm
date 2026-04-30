from rest_framework import serializers

from apps.products.models import ProductBatch, ProductUnitMeasurement, ProductLocation, Product


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



from rest_framework import serializers

class StoreQuantitySerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    store_name = serializers.CharField()
    quantity = serializers.IntegerField()


class ProductBatchListSerializer(serializers.ModelSerializer):
    my_quantity = serializers.SerializerMethodField()
    other_stores = serializers.SerializerMethodField()
    # selling_price = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            'category',
            # "barcode",
            "name",
            "unit_measurement",
            "description",
            # "selling_price",
            "my_quantity",
            "other_stores",
        ]

    def get_my_quantity(self, obj):
        store = self.context["selected_store"]

        for b in obj.all_batches:
            if b.store_id == store.id:
                return b.quantity

        return 0

    def get_other_stores(self, obj):
        store = self.context["selected_store"]

        my_qty = 0
        result = []

        for b in obj.all_batches:
            if b.store_id == store.id:
                my_qty = b.quantity
            elif b.quantity > 0:
                result.append({
                    "store_id": b.store_id,
                    "store_name": b.store.name,
                    "quantity": b.quantity
                })

        # ❗ faqat o‘z storeda yo‘q bo‘lsa boshqalarni ko‘rsatamiz
        if my_qty > 0:
            return []

        return result