from django.db import transaction
from django.core.exceptions import ValidationError
from apps.contract.models import Supplier


class SupplierService:

    @staticmethod
    @transaction.atomic
    def create_supplier(*, request_user, data: dict):

        # 🔴 AUTH
        if not request_user.is_superuser:
            raise ValidationError("Only superuser can create supplier")

        return Supplier.objects.create(**data)

    @staticmethod
    @transaction.atomic
    def update_supplier(*, request_user, instance: Supplier, data: dict):

        if not request_user.is_superuser:
            raise ValidationError("Only superuser can update supplier")

        for field, value in data.items():
            setattr(instance, field, value)

        instance.save()
        return instance

    @staticmethod
    @transaction.atomic
    def delete_supplier(*, request_user, instance: Supplier):

        if not request_user.is_superuser:
            raise ValidationError("Only superuser can delete supplier")

        instance.delete()