from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.contract.models import Supplier

from django.utils import translation

from apps.users.validations import check_valid_phone


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


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = (
            'id', 'name_uz', 'name_uz_cyrl', 'description_uz', 'description_uz_cyrl',
             'address_uz', 'address_uz_cyrl', 'phone_number', 'inn', 'is_active',
        )

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

    def validate_inn(self, inn):
        if Supplier.objects.filter(inn=inn).exists():
            raise serializers.ValidationError("Supplier with this INN already exists")

        if not inn.isdigit():
            raise ValidationError("Incorrect INN")

        return inn

    def validate(self, data):
        phone_number = data.get('phone_number')

        check_valid_phone(phone_number)
        return data

