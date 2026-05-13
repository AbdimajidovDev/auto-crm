from django.utils import translation
from rest_framework import serializers
from apps.store.models import Store
from apps.users.validations import check_valid_phone


class StoreSellerSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="user.id")
    full_name = serializers.CharField(source="user.full_name")
    phone_number = serializers.CharField(source="user.phone_number")


class StoreListSerializer(serializers.ModelSerializer):

    # ⚠️ MUAMMO [PERFORMANCE]: `sellers` SerializerMethodField har store uchun user_links query ishlatishi mumkin.
    # Sabab: selector/view `prefetch_related("user_links__user")` qilmasa `obj.user_links.filter(...)` DBga boradi.
    # Natija: Store listda N+1 query yuzaga keladi.
    # ✅ YECHIM:
    # sellers = StoreSellerSerializer(source="active_user_links", many=True, read_only=True)
    # Viewda: Prefetch("user_links", queryset=StoreUser.objects.filter(is_active=True).select_related("user"), to_attr="active_user_links")
    sellers = serializers.SerializerMethodField()

    class Meta:
        model = Store
        fields = (
            'id', 'type', 'name', 'phone_number',
            'address', 'latitude', 'longitude',
            'is_active', 'sellers'
        )

    def get_sellers(self, obj):
        store_users = obj.user_links.filter(is_active=True).select_related("user")

        return StoreSellerSerializer(store_users, many=True).data



class StoreCreateSerializer(serializers.Serializer):
    name_uz = serializers.CharField(max_length=255)
    name_uz_cyrl = serializers.CharField(max_length=255)
    phone_number = serializers.CharField(max_length=20)
    address_uz = serializers.CharField()
    address_uz_cyrl = serializers.CharField()

    type = serializers.ChoiceField(choices=Store.StoreType.choices)

    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False)

    def validate(self, attrs):
        phone_number = attrs.get("phone_number")
        lat = attrs.get("latitude")
        lon = attrs.get("longitude")

        check_valid_phone(phone_number)

        if (lat is None) != (lon is None):
            raise serializers.ValidationError(
                "Latitude va longitude birga yuborilishi kerak"
            )

        return attrs


class StoreResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = "__all__"


class StoreDetailSerializer(serializers.ModelSerializer):
    # ⚠️ MUAMMO [PERFORMANCE]: Detail serializer ham `user_links.filter()` orqali DBga murojaat qiladi.
    # Sabab: detail queryset prefetch qilinmasa sellerlar uchun qo'shimcha query bo'ladi.
    # Natija: detail response barqaror query budgetga ega emas.
    # ✅ YECHIM:
    # get_object querysetida `prefetch_related(Prefetch("user_links", ...))` ishlatish.
    sellers = serializers.SerializerMethodField()

    class Meta:
        model = Store
        fields = (
            'id', 'name_uz', 'name_uz_cyrl', 'phone_number', 'address_uz', 'address_uz_cyrl',
            'type', 'latitude', 'longitude', 'is_active', 'sellers'
        )

    def get_sellers(self, obj):
        store_users = obj.user_links.filter(is_active=True).select_related("user")

        return StoreSellerSerializer(store_users, many=True).data


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 2
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Store querysetlarga active user_links prefetch qo'shish]
# ═══════════════════════════════
