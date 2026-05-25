from django.db import transaction
from django.shortcuts import get_object_or_404

from apps.products.models import Brand


class BrandService:

    @staticmethod
    @transaction.atomic
    def create(*, validated_data):
        return Brand.objects.create(
            **validated_data
        )

    @staticmethod
    @transaction.atomic
    def update(*, instance, validated_data):
        for field, value in validated_data.items():
            setattr(
                instance,
                field,
                value,
            )

        instance.save()

        return instance

    @staticmethod
    @transaction.atomic
    def delete(*, brand_id):
        brand = get_object_or_404(
            Brand,
            id=brand_id,
        )

        brand.delete()

    @staticmethod
    def get(brand_id):
        return get_object_or_404(
            Brand,
            id=brand_id,
        )

    @staticmethod
    def list():
        return Brand.objects.all()
