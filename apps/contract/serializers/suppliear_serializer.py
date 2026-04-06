from rest_framework import serializers
from apps.contract.models import Supplier

from django.utils import translation


class SupplierGetSerializer(serializers.ModelSerializer):
    # name = serializers.SerializerMethodField()
    # description = serializers.SerializerMethodField()
    # address = serializers.SerializerMethodField()

    class Meta:
        model = Supplier
        fields = [
            "id",
            "name",
            "description",
            "address",
            "phone_number",
            "inn",
            "is_active",
        ]

    # def _get_field(self, obj, field):
    #     lang = translation.get_language() or "uz"
    #     return getattr(obj, f"{field}_{lang}", getattr(obj, field))
    #
    # def get_name(self, obj):
    #     return self._get_field(obj, "name")
    #
    # def get_description(self, obj):
    #     return self._get_field(obj, "description")
    #
    # def get_address(self, obj):
    #     return self._get_field(obj, "address")
    #


class SupplierCreateSerializer(serializers.ModelSerializer):

    class Meta:
        model = Supplier
        fields = [
            "phone_number",
            "inn",

            # translations
            "name_uz",
            "name_uz_cyrl",
            "description_uz",
            "description_uz_cyrl",
            "address_uz",
            "address_uz_cyrl",
        ]

    def validate_inn(self, value):
        if Supplier.objects.filter(inn=value).exists():
            raise serializers.ValidationError("Supplier with this INN already exists")
        return value