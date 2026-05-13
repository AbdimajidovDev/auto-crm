from rest_framework import serializers

from apps.products.models import Category


# ─────────────────────────────────────────────
# SERIALIZER
# ─────────────────────────────────────────────

class CategoryListSerializer(serializers.ModelSerializer):

    # ✅ YAXSHI: List serializer write maydonlardan ajratilgan, response faqat kerakli o'qish maydonlarini beradi.
    class Meta:
        model = Category
        fields = (
            "id",
            "slug",
            "name",
            "description",
            "image",
            "created_at",
        )

# class CategoryListSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Category
#         fields = (
#             'id', 'slug', 'name', 'description', 'image', 'created_at',
#         )


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


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Kategoriyalar uchun serializer ajratilishi hozircha yetarli]
# ═══════════════════════════════
