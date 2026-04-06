from django.utils import translation
from rest_framework import serializers
from apps.store.models import Store



class StoreSellerSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="user.id")
    full_name = serializers.CharField(source="user.full_name")
    phone_number = serializers.CharField(source="user.phone_number")


class StoreListSerializer(serializers.ModelSerializer):

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
        lat = attrs.get("latitude")
        lon = attrs.get("longitude")

        if (lat is None) != (lon is None):
            raise serializers.ValidationError(
                "Latitude va longitude birga yuborilishi kerak"
            )

        return attrs


class StoreResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = "__all__"

