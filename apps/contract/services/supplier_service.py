from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.contract.models import Supplier, SupplierTransaction


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


class SupplierPaymentService:

    @staticmethod
    @transaction.atomic
    def make_payment(*, supplier, entry, amount, note, user):
        # 1. To'lov tranzaksiyasini yaratish
        payment_transaction = SupplierTransaction.objects.create(
            supplier=supplier,
            entry=entry,
            amount=amount,
            type=SupplierTransaction.TransactionType.PAYMENT,
            note=note or f"Taminotchiga to'lov amalga oshirildi. Mas'ul: {user.full_name}"
        )

        # 2. Agar Supplier modelida jami balance maydoni bo'lsa, uni yangilaymiz:
        # Supplier.objects.filter(id=supplier.id).update(balance=F('balance') - amount)

        return payment_transaction
