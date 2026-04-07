from rest_framework import serializers

from apps.products.models import Category




class CategoryListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = (
            'id', 'slug', 'name', 'description', 'image', 'created_at',
        )


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = (
            'id', 'name_uz', 'name_uz_cyrl', 'description_uz', 'description_uz_cyrl', 'image'
        )


class CategoryDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = (
            'id', 'slug', 'name_uz', 'name_uz_cyrl', 'description_uz', 'description_uz_cyrl', 'image'
        )
