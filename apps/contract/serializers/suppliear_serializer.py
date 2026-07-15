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
            "created_at",
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
             'created_at',
        )

class SupplierCreateSerializer(serializers.ModelSerializer):
    # INN ixtiyoriy: bo'sh kelsa NULL sifatida saqlanadi (unique bo'sh satrlarda to'qnashmasligi uchun)
    inn = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = Supplier
        fields = [
            "phone_number", "inn",
            "name_uz", "name_uz_cyrl", "description_uz", "description_uz_cyrl", "address_uz", "address_uz_cyrl",
        ]

    def validate_inn(self, inn):
        inn = (inn or "").strip()
        if not inn:
            return None

        if not inn.isdigit():
            raise ValidationError("Incorrect INN")

        # Tahrirlashda ta'minotchining o'z INN'i "mavjud" deb hisoblanmasligi kerak
        qs = Supplier.objects.filter(inn=inn)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Supplier with this INN already exists")

        return inn

    def validate(self, data):
        phone_number = data.get('phone_number')

        # partial update'da telefon yuborilmasa tekshirilmaydi (create'da maydon majburiy)
        if phone_number is not None:
            check_valid_phone(phone_number)
        return data
