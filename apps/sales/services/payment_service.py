"""
To'lovlarni yozishning yagona markaziy nuqtasi.

Sotuv yaratish, qarz to'lash va qaytarish (refund) oqimlari Payment yozuvlarini
faqat shu service orqali yaratadi — bank_card/type invariantlari va
payment_type qayta hisoblash qoidasi hech qayerda takrorlanmaydi.
"""

import uuid
from decimal import Decimal

from rest_framework import serializers

from apps.sales.models import Payment


def validate_payment_method(payment_type: str, bank_card) -> None:
    """
    Serializer darajasidagi umumiy qoida (sale create, sale return, pay debt):
      type=card  → bank_card majburiy
      type=cash  → bank_card bo'sh bo'lishi shart
    Model darajasida ham xuddi shu invariant Payment.clean() da bor.
    """
    if payment_type == Payment.Type.CARD and bank_card is None:
        raise serializers.ValidationError({
            "bank_card": "Karta to'lovi uchun bank_card majburiy"
        })
    if payment_type == Payment.Type.CASH and bank_card is not None:
        raise serializers.ValidationError({
            "bank_card": "Naqd to'lovda bank_card yuborilmasligi kerak"
        })


class SalePaymentService:

    @staticmethod
    def record_payments(*, sale, payments_data, customer=None, is_refund=False, payment_group=None) -> Decimal:
        """
        To'lovlar ro'yxatini bitta bulk_create bilan yozadi va jami summani qaytaradi.

        payments_data: [{"type": "cash"|"card", "amount": Decimal, "bank_card": BankCard|None}, ...]
        is_refund=True — mijozga qaytarilgan pul (sale return oqimi).
        payment_group — tashqaridan berilsa (masalan, SaleReturn o'ziga bog'lash uchun)
        shu guruh ishlatiladi, aks holda yangi guruh yaratiladi.

        Diqqat: payment_type ni qayta hisoblash chaqiruvchi tomonda —
        sale.recalculate_payment_type() (har bir oqim o'z transaction nuqtasida chaqiradi).
        """
        # Bitta chaqiruv = bitta to'lov harakati — barcha qatorlar bitta guruhda
        if payment_group is None:
            payment_group = uuid.uuid4()
        payments = [
            Payment(
                sale=sale,
                customer=customer,
                amount=p["amount"],
                type=p["type"],
                bank_card=p.get("bank_card"),
                is_refund=is_refund,
                payment_group=payment_group,
            )
            for p in payments_data
        ]

        # bulk_create save() ni chaqirmaydi — model invariantini qo'lda tekshiramiz
        for payment in payments:
            payment.clean()

        Payment.objects.bulk_create(payments)

        return sum((Decimal(str(p.amount)) for p in payments), Decimal("0"))
