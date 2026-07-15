from decimal import Decimal

from django.db import transaction
# DRF ValidationError — view'da avtomatik 400 qaytadi (django'niki 500 berardi)
from rest_framework.exceptions import ValidationError
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

        return DebtService._apply_sale_payment(
            sale=sale,
            sale_debt=current_debt,
            amount=amount,
            payment_type=payment_type,
            bank_card=bank_card,
        )

    @staticmethod
    def _apply_sale_payment(*, sale, sale_debt, amount, payment_type, bank_card=None):
        """
        Bitta sotuvga qarz to'lovini qo'llaydi (sale allaqachon qulflangan bo'lishi kerak):
          1. Payment yozuvi (tarix: qachon, qancha, qanday usulda to'langani)
          2. CustomerDebt DECREASE (qarz balansi kamayadi)
          3. sale.paid_amount / status yangilanadi — ro'yxat va mijoz modalidagi
             (total - paid) formulasi to'lovdan keyin ham to'g'ri ko'rsatishi uchun
          4. payment_type qayta hisoblanadi (masalan, debt → cash/mixed)
        """
        payment = Payment.objects.create(
            customer=sale.customer,
            amount=amount,
            type=payment_type,
            bank_card=bank_card,
            sale=sale,
        )

        CustomerDebt.objects.create(
            customer=sale.customer,
            sale=sale,
            amount=amount,
            type=CustomerDebt.Type.DECREASE,
        )

        sale.paid_amount = (sale.paid_amount or Decimal("0")) + amount
        sale.status = Sale.Status.PAID if amount >= sale_debt else Sale.Status.PARTIAL
        sale.save(update_fields=["paid_amount", "status"])

        # Qarz to'lovi sotuvning to'lov tarkibini o'zgartiradi (masalan, debt → card/mixed)
        sale.recalculate_payment_type()

        return payment

    @staticmethod
    @transaction.atomic
    def pay_customer_debt(*, customer_id, amount, payment_type, bank_card=None):
        """
        Mijozning UMUMIY qarzini FIFO tartibida yopadi: to'lov ENG ESKI qarzli
        sotuvdan boshlab taqsimlanadi.

        Misol: mijozda 2 ta 50 so'mlik qarzli buyurtma bor (jami 100). 90 so'm
        to'lasa — birinchi (eski) buyurtma to'liq yopiladi (50), ikkinchisiga
        40 yoziladi, 10 so'm qarz qoladi.

        Har bir taqsimot alohida Payment yozuvi bilan saqlanadi (qachon va
        qancha to'langani tarixda qoladi). Umumiy qarzdan ortiq to'lov rad etiladi.
        """
        if amount <= 0:
            raise ValidationError("Miqdor ijobiy bo'lishi kerak")

        # Mijozning barcha sotuvlari qulflanadi — parallel ikki to'lov
        # bir qarzni ikki marta yopib yubormasligi uchun
        sales = list(
            Sale.objects.select_for_update()
            .select_related("customer")
            .filter(customer_id=customer_id)
            .order_by("created_at", "id")
        )

        debt_sales = []
        total_debt = Decimal("0")
        for sale in sales:
            sale_debt = DebtService.get_sale_debt(sale)
            if sale_debt > 0:
                debt_sales.append((sale, sale_debt))
                total_debt += sale_debt

        if total_debt <= 0:
            raise ValidationError("Mijozda qarz yo'q")
        if amount > total_debt:
            raise ValidationError(
                f"To'lov summasi umumiy qarzdan oshib ketdi. Qoldiq qarz: {total_debt:.2f}"
            )

        remaining = amount
        allocations = []
        for sale, sale_debt in debt_sales:
            if remaining <= 0:
                break
            alloc = min(sale_debt, remaining)
            payment = DebtService._apply_sale_payment(
                sale=sale,
                sale_debt=sale_debt,
                amount=alloc,
                payment_type=payment_type,
                bank_card=bank_card,
            )
            allocations.append({
                "sale": sale.id,
                "payment_id": payment.id,
                "amount": f"{alloc:.2f}",
                "closed": alloc >= sale_debt,
                "sale_debt_left": f"{(sale_debt - alloc):.2f}",
            })
            remaining -= alloc

        return {
            "paid": f"{amount:.2f}",
            "remaining_debt": f"{(total_debt - amount):.2f}",
            "allocations": allocations,
        }

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
