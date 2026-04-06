from rest_framework import serializers

from apps.products.models import Product
from apps.products.serializers.category_crud_serializer import CategorySerializer



class ProductListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = "__all__"


class ProductCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = (
            'id', 'category', 'name_uz', 'name_uz_cyrl', 'description_uz', 'description_uz_cyrl',
            'quantity', 'price', 'image',  'created_at'
        )
        # exclude = ["barcode", "shtrix_code"]

    def validate(self, data):
        if data.get("price") <= 0:
            raise serializers.ValidationError("Invalid price")

        return data



class ProductGetSerializer(serializers.ModelSerializer):
    category_name = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id", "name", "description", "price", "category", "category_name", "barcode", "shtrix_code", "image", "created_at"
        )

    def get_category_name(self, obj):
        return obj.category.name if obj.category else None