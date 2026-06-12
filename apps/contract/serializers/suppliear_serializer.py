from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.contract.models import Supplier
from apps.users.validations import check_valid_phone

from decimal import Decimal


class SupplierListSerializer(serializers.ModelSerializer):
    total_purchase_amount = serializers.DecimalField(
        max_digits=15,
        decimal_places=2,
        read_only=True,
        default=Decimal("0.00"),
    )
    total_debt = serializers.DecimalField(
        max_digits=15,
        decimal_places=2,
        read_only=True,
        default=Decimal("0.00"),
    )

    class Meta:
        model = Supplier
        fields = [
            "id", "name", "description", "address", "phone_number",
            "inn", "is_active", "total_purchase_amount", "total_debt",
        ]


class SupplierGetSerializer(serializers.ModelSerializer):

    class Meta:
        model = Supplier
        fields = [
            "id", "name", "description", "address", "phone_number", "inn", "is_active",
        ]


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
            "phone_number", "inn",
            "name_uz", "name_uz_cyrl", "description_uz", "description_uz_cyrl", "address_uz", "address_uz_cyrl",
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
