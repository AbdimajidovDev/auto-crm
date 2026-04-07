from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.products.models import Product, ProductImage, ProductBatch



class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ('image', 'product')


class ProductBatchSerializer(serializers.ModelSerializer):
    store_name = serializers.SerializerMethodField()

    class Meta:
        model = ProductBatch
        fields = (
            'id', 'product', 'store', 'store_name', 'quantity',
            'purchase_price', 'selling_price', 'barcode', 'shtrix_code'
        )

    def get_store_name(self, obj):
        return obj.store.name if obj.store else None


class ProductListSerializer(serializers.ModelSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    category_name = serializers.SerializerMethodField()
    batches = ProductBatchSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = (
            'id', 'category', 'category_name', 'name', 'description', 'is_active', 'created_at', 'images', 'batches'
        )

    def get_category_name(self, obj):
        return obj.category.name if obj.category else None


class ProductCreateSerializer(serializers.ModelSerializer):
    images = serializers.ListField(
        child=serializers.ImageField(),
        write_only=True,
        required=False
    )

    class Meta:
        model = Product
        fields = (
            'id', 'category', 'name_uz', 'name_uz_cyrl', 'description_uz', 'description_uz_cyrl', 'images'
        )

    def validate_images(self, images):
        if len(images) > 7:
            raise ValidationError("Max 7 images allowed")




class ProductGetSerializer(serializers.ModelSerializer):
    category_name = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id", "name", "description", "category", "category_name", "created_at"
        )

    def get_category_name(self, obj):
        return obj.category.name if obj.category else None

