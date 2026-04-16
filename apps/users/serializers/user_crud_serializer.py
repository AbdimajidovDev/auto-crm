# users/serializers.py
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.store.models import Store
from apps.users.models import User
from apps.users.validations import check_valid_phone
from django.core.exceptions import ValidationError as DjangoValidationError


class UserSerializer(serializers.ModelSerializer):
    store_id = serializers.SerializerMethodField()
    store_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id", "full_name", "phone_number", "email",
            "is_active", "created_at", "updated_at", 'store_id', 'store_name',
        )

    def validate_phone_number(self, phone):
        check_valid_phone(phone)
        return phone

    def get_store_id(self, obj):
        if hasattr(obj, "active_store_links") and obj.active_store_links:
            return obj.active_store_links[0].store.id
        return None

    def get_store_name(self, obj):
        if hasattr(obj, "active_store_links") and obj.active_store_links:
            return obj.active_store_links[0].store.name
        return None



class SellerCreateSerializer(serializers.Serializer):
    full_name = serializers.CharField(write_only=True)
    phone_number = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)
    store_id = serializers.IntegerField(write_only=True)

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value

    def validate(self, data):
        phone_number = data.get('phone_number')
        password = data.get('password')
        confirm_password = data.get('confirm_password')

        check_valid_phone(phone_number)

        if password != confirm_password:
            raise serializers.ValidationError({"error": "Passwords don't match"})

        data.pop("confirm_password")
        return data


class UserResponseSerializer(serializers.ModelSerializer):
    store = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "phone_number", "full_name", "store"]

    def get_store(self, obj):
        store_link = (
            obj.store_links
            .filter(is_active=True)
            .select_related("store")
            .first()
        )

        if not store_link:
            return None

        return store_link.store.name