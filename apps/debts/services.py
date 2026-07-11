from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.debts.models import CustomerDebt
from apps.sales.models import Sale, Payment


class DebtService:

    @staticmethod
    def get_sale_debt(sale):
        # ⚠️ MUAMMO [PERFORMANCE]: Qarz balansi uchun ikki alohida aggregate query ishlatilgan.
        # Sabab: increase va decrease alohida `filter().aggregate()` bilan hisoblanadi.
        # Natija: `pay_debt` har chaqirilganda ortiqcha DB round-trip paydo bo'ladi.
        # ✅ YECHIM:
        # balance = CustomerDebt.objects.filter(sale=sale).aggregate(
        #     total=Sum(Case(
        #         When(type=CustomerDebt.Type.INCREASE, then=F("amount")),
        #         When(type=CustomerDebt.Type.DECREASE, then=-F("amount")),
        #         default=0,
        #         output_field=DecimalField(),
        #     ))
        # )["total"] or 0
        # OPTIMIZATION: ikki alohida `filter().aggregate()` o'rniga bitta querysetda `Case/When`
        # bilan bitta aggregate qilish DB yukini kamaytiradi.
        increases = CustomerDebt.objects.filter(
            sale=sale,
            type=CustomerDebt.Type.INCREASE
        ).aggregate(total=Sum("amount"))["total"] or 0

        decreases = CustomerDebt.objects.filter(
            sale=sale,
            type=CustomerDebt.Type.DECREASE
        ).aggregate(total=Sum("amount"))["total"] or 0

        return increases - decreases

    @staticmethod
    @transaction.atomic
    def pay_debt(*, sale_id, amount, payment_type, bank_card=None):

        # 🔴 LOCK SALE (critical!)
        # ⚠️ MUAMMO [PERFORMANCE]: `select_related("customer")` ishlatilmagan.
        # Sabab: keyingi kodda `sale.customer` payment va debt yozishda ishlatiladi.
        # Natija: customer uchun qo'shimcha SELECT query chiqadi.
        # ✅ YECHIM:
        # sale = Sale.objects.select_for_update().select_related("customer").get(id=sale_id)
        # sale = Sale.objects.select_for_update().select_related("customer").get(id=sale_id)
        # N+1 emas, lekin `select_related("customer")` qo'shilsa keyingi `sale.customer` murojaatlari
        # qo'shimcha so'rovsiz ishlaydi (hozirgi kodda customer tegishli joylar uchun).
        sale = Sale.objects.select_for_update().get(id=sale_id)
        # customer = sale.customer

        # if not customer:
        #     raise ValidationError("Sale mijozga bog'lanmagan")

        if amount <= 0:
            raise ValidationError("Miqdor ijobiy bo'lishi kerak")

        current_debt = DebtService.get_sale_debt(sale)

        if current_debt <= 0:
            raise ValidationError("Bu sotuvda qarz yo'q")

        if amount > current_debt:
            raise ValidationError("Miqdor qarzdan oshib ketdi")

        # 🔴 PAYMENT
        payment = Payment.objects.create(
            customer=sale.customer,
            amount=amount,
            type=payment_type,
            bank_card=bank_card,
            sale=sale  # 🔥 MUHIM
        )

        # 🔴 DEBT REDUCE (SALE BILAN)
        CustomerDebt.objects.create(
            customer=sale.customer,
            sale=sale,
            amount=amount,
            type=CustomerDebt.Type.DECREASE
        )

        # Qarz to'lovi sotuvning to'lov tarkibini o'zgartiradi (masalan, debt → card/mixed)
        sale.recalculate_payment_type()

        return payment

    @staticmethod
    @transaction.atomic
    def increase_debt(*, customer, sale, amount, due_date=None):
        if not customer:
            raise ValidationError("Customer bo'lishi kerak")
        if amount <= 0:
            raise ValidationError("Amount > 0 bo'lishi kerak")
        return CustomerDebt.objects.create(
            customer=customer,
            sale=sale,
            amount=amount,
            type=CustomerDebt.Type.INCREASE,
            due_date=due_date
        )

    @staticmethod
    @transaction.atomic
    def decrease_debt(*, customer, sale, amount):

        if not customer:
            raise ValidationError("Customer bo‘lishi kerak")

        if amount <= 0:
            raise ValidationError("Amount > 0 bo‘lishi kerak")

        return CustomerDebt.objects.create(
            customer=customer,
            sale=sale,
            amount=amount,
            type=CustomerDebt.Type.DECREASE
        )


class CustomerDebtService:

    @staticmethod
    def get(store_ids):
        # ⚠️ MUAMMO [KRITIK]: Qarz summasi `type` farqini hisobga olmasdan `Sum("amount")` qilinyapti.
        # Sabab: increase va decrease yozuvlari bir xil ishora bilan qo'shiladi.
        # Natija: mijoz qarzi noto'g'ri, odatda oshirib ko'rsatiladi.
        # ✅ YECHIM:
        # return qs.values("customer__full_name").annotate(
        #     debt=Sum(Case(
        #         When(type=CustomerDebt.Type.INCREASE, then=F("amount")),
        #         When(type=CustomerDebt.Type.DECREASE, then=-F("amount")),
        #         default=0,
        #         output_field=DecimalField(),
        #     ))
        # )
        # MUAMMO: `debt=Sum("amount")` `type=i/d` farqin hisobga olmaydi — qarz balansi noto'g'ri chiqishi mumkin;
        # `DebtService.customer_debt` dagidek `Case/When` yoki alohida inc/dec kerak.
        qs = CustomerDebt.objects.all()

        if store_ids:
            qs = qs.filter(sale__store_id__in=store_ids)

        return qs.values("customer__full_name").annotate(
            debt=Sum("amount")
        )


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 2
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [CustomerDebtService.get balans hisobini Case/When bilan to'g'rilash]
# ═══════════════════════════════
