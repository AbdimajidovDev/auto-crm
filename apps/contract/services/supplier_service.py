from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from rest_framework.exceptions import ValidationError

from apps.contract.models import StockEntry, Supplier, SupplierTransaction


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
    def get_remaining_debt(entry) -> Decimal:
        """Kirim bo'yicha qoldiq qarz: kirim (in) - to'langan (pay)."""
        totals = SupplierTransaction.objects.filter(entry=entry).aggregate(
            total_in=Sum("amount", filter=Q(type=SupplierTransaction.TransactionType.INVENTORY_IN)),
            total_paid=Sum("amount", filter=Q(type=SupplierTransaction.TransactionType.PAYMENT)),
        )
        zero = Decimal("0")
        return (totals["total_in"] or zero) - (totals["total_paid"] or zero)

    @staticmethod
    @transaction.atomic
    def make_payment(*, supplier, entry, amount, note, user, payment_method="cash", bank_card=None):
        # Entry qatori qulflanadi — bir vaqtda ikkita to'lov qoldiqdan
        # oshib ketmasligi uchun (tekshiruv va yozish bitta tranzaksiyada)
        locked_entry = StockEntry.objects.select_for_update().get(pk=entry.pk)

        remaining = SupplierPaymentService.get_remaining_debt(locked_entry)
        if remaining <= 0:
            raise ValidationError({"amount": "Bu kirim bo'yicha qarz yo'q"})
        if amount > remaining:
            raise ValidationError({
                "amount": f"To'lov qoldiq qarzdan oshib ketdi. Qoldiq qarz: {remaining:.2f}"
            })

        # To'lov usuli izoh uchun: "naqd" yoki karta nomi (Uzcard/Humo/...)
        method_label = bank_card.name if bank_card else "naqd"

        # To'lov tranzaksiyasini yaratish
        payment_transaction = SupplierTransaction.objects.create(
            supplier=supplier,
            entry=locked_entry,
            amount=amount,
            type=SupplierTransaction.TransactionType.PAYMENT,
            payment_method=payment_method,
            bank_card=bank_card,
            note=note or f"Taminotchiga to'lov ({method_label}). Mas'ul: {user.full_name}"
        )

        return payment_transaction
