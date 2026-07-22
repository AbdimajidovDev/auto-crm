import uuid
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
    def make_payments(*, supplier, entry, payments, note, user):
        """
        Bitta so'rovda bir nechta usul bilan qarz to'lash (split) — har usul
        alohida SupplierTransaction (pay) qatori bo'lib yoziladi.

        payments: [{"type": "cash"|"card", "amount": Decimal, "bank_card": BankCard|None}, ...]
        Qoldiq qarz tekshiruvi bitta qulf ostida jami summa bo'yicha bajariladi.
        """
        payments = [p for p in payments if p["amount"] > 0]
        if not payments:
            raise ValidationError({"payments": "To'lov qatorlari bo'sh"})

        total = sum((Decimal(str(p["amount"])) for p in payments), Decimal("0"))

        # Entry qatori qulflanadi — bir vaqtda ikkita to'lov qoldiqdan
        # oshib ketmasligi uchun (tekshiruv va yozish bitta tranzaksiyada)
        locked_entry = StockEntry.objects.select_for_update().get(pk=entry.pk)

        remaining = SupplierPaymentService.get_remaining_debt(locked_entry)
        if remaining <= 0:
            raise ValidationError({"amount": "Bu xarid bo'yicha qarz yo'q"})
        if total > remaining:
            raise ValidationError({
                "amount": f"To'lov qoldiq qarzdan oshib ketdi. Qoldiq qarz: {remaining:.2f}"
            })

        # Bitta chaqiruv = bitta to'lov harakati — barcha qatorlar bitta guruhda
        payment_group = uuid.uuid4()
        transactions = []
        for p in payments:
            bank_card = p.get("bank_card")
            # To'lov usuli izoh uchun: "naqd" yoki karta nomi (Uzcard/Humo/...)
            method_label = bank_card.name if bank_card else "naqd"
            transactions.append(SupplierTransaction.objects.create(
                supplier=supplier,
                entry=locked_entry,
                amount=p["amount"],
                type=SupplierTransaction.TransactionType.PAYMENT,
                payment_method=p["type"],
                bank_card=bank_card,
                payment_group=payment_group,
                note=note or f"Taminotchiga to'lov ({method_label}). Mas'ul: {user.full_name}"
            ))

        return transactions

    @staticmethod
    def make_payment(*, supplier, entry, amount, note, user, payment_method="cash", bank_card=None):
        """Eski (bitta usulli) interfeys — ichkarida split servisga o'tkazadi."""
        return SupplierPaymentService.make_payments(
            supplier=supplier,
            entry=entry,
            payments=[{"type": payment_method, "amount": amount, "bank_card": bank_card}],
            note=note,
            user=user,
        )[0]
