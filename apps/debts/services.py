import uuid
from decimal import Decimal

from django.db import transaction
# DRF ValidationError — view'da avtomatik 400 qaytadi (django'niki 500 berardi)
from rest_framework.exceptions import ValidationError
from django.db.models import Case, DecimalField, F, Sum, Value, When

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
    def _normalize_payment_chunks(*, payments=None, amount=None, payment_type=None, bank_card=None):
        """
        Split (payments ro'yxati) yoki eski bitta usulli argumentlarni yagona
        shaklga keltiradi: [{"type", "amount", "bank_card"}, ...].
        """
        if payments:
            chunks = [
                {
                    "type": p["type"],
                    "amount": Decimal(str(p["amount"])),
                    "bank_card": p.get("bank_card"),
                }
                for p in payments
                if Decimal(str(p["amount"])) > 0
            ]
        elif amount is not None and payment_type is not None:
            chunks = [{"type": payment_type, "amount": Decimal(str(amount)), "bank_card": bank_card}]
        else:
            chunks = []

        if not chunks:
            raise ValidationError("To'lov qatorlari bo'sh")
        return chunks

    @staticmethod
    @transaction.atomic
    def pay_debt(*, sale_id, amount=None, payment_type=None, bank_card=None, payments=None):
        """
        Bitta sotuvning qarzini to'lash. Split rejimda payments ro'yxati
        beriladi (har usul alohida Payment qatori bo'lib yoziladi), eski
        rejimda amount + payment_type + bank_card.
        """
        chunks = DebtService._normalize_payment_chunks(
            payments=payments, amount=amount, payment_type=payment_type, bank_card=bank_card
        )
        total = sum((c["amount"] for c in chunks), Decimal("0"))

        # 🔴 LOCK SALE (critical!)
        # Diqqat: select_related("customer") qo'shib bo'lmaydi — customer nullable FK,
        # Postgres "FOR UPDATE cannot be applied to the nullable side of an outer join" beradi
        sale = Sale.objects.select_for_update().get(id=sale_id)

        if total <= 0:
            raise ValidationError("Miqdor ijobiy bo'lishi kerak")

        current_debt = DebtService.get_sale_debt(sale)

        if current_debt <= 0:
            raise ValidationError("Bu sotuvda qarz yo'q")

        if total > current_debt:
            raise ValidationError("Miqdor qarzdan oshib ketdi")

        return DebtService._apply_sale_payment(
            sale=sale,
            sale_debt=current_debt,
            chunks=chunks,
        )[0]

    @staticmethod
    def _apply_sale_payment(*, sale, sale_debt, chunks):
        """
        Bitta sotuvga qarz to'lovini qo'llaydi (sale allaqachon qulflangan bo'lishi kerak):
          1. Har bir split qator uchun Payment yozuvi (tarix: qachon, qancha,
             qaysi usul/karta bilan to'langani)
          2. CustomerDebt DECREASE (qarz balansi jami summaga kamayadi)
          3. sale.paid_amount / status yangilanadi — ro'yxat va mijoz modalidagi
             (total - paid) formulasi to'lovdan keyin ham to'g'ri ko'rsatishi uchun
          4. payment_type qayta hisoblanadi (masalan, debt → cash/mixed)

        chunks: [{"type": "cash"|"card", "amount": Decimal, "bank_card": BankCard|None}, ...]
        Qaytaradi: yaratilgan Payment yozuvlari ro'yxati.
        """
        total = sum((c["amount"] for c in chunks), Decimal("0"))

        # Bitta to'lov harakati — barcha split qatorlar bitta guruhda
        # (UI tarixda bitta blok qilib, qismlarini ichida ko'rsatadi)
        payment_group = uuid.uuid4()
        created_payments = [
            Payment.objects.create(
                customer=sale.customer,
                amount=c["amount"],
                type=c["type"],
                bank_card=c.get("bank_card"),
                sale=sale,
                payment_group=payment_group,
                is_debt_payment=True,
            )
            for c in chunks
        ]

        CustomerDebt.objects.create(
            customer=sale.customer,
            sale=sale,
            amount=total,
            type=CustomerDebt.Type.DECREASE,
        )

        sale.paid_amount = (sale.paid_amount or Decimal("0")) + total
        sale.status = Sale.Status.PAID if total >= sale_debt else Sale.Status.PARTIAL
        sale.save(update_fields=["paid_amount", "status"])

        # Qarz to'lovi sotuvning to'lov tarkibini o'zgartiradi (masalan, debt → card/mixed)
        sale.recalculate_payment_type()

        return created_payments

    @staticmethod
    @transaction.atomic
    def pay_customer_debt(*, customer_id, amount=None, payment_type=None, bank_card=None, payments=None):
        """
        Mijozning UMUMIY qarzini FIFO tartibida yopadi: to'lov ENG ESKI qarzli
        sotuvdan boshlab taqsimlanadi.

        Split rejimda payments ro'yxati "hovuzlar" sifatida ishlatiladi:
        har bir sotuv taqsimotiga hovuzlardan navbat bilan (naqd, keyin
        kartalar) pul olinadi — shunda umumiy naqd/karta yig'indilari foydalanuvchi
        kiritganiga aynan teng bo'ladi, har bir sotuvda esa aniq qaysi usuldan
        qancha to'langani Payment qatorlarida qoladi.

        Misol: mijozda 2 ta 50 so'mlik qarzli buyurtma bor (jami 100). 90 so'm
        to'lasa — birinchi (eski) buyurtma to'liq yopiladi (50), ikkinchisiga
        40 yoziladi, 10 so'm qarz qoladi.

        Umumiy qarzdan ortiq to'lov rad etiladi.
        """
        chunks = DebtService._normalize_payment_chunks(
            payments=payments, amount=amount, payment_type=payment_type, bank_card=bank_card
        )
        total_payment = sum((c["amount"] for c in chunks), Decimal("0"))

        if total_payment <= 0:
            raise ValidationError("Miqdor ijobiy bo'lishi kerak")

        # Mijozning barcha sotuvlari qulflanadi — parallel ikki to'lov
        # bir qarzni ikki marta yopib yubormasligi uchun.
        # Diqqat: select_related("customer") qo'shib bo'lmaydi — customer nullable FK,
        # Postgres "FOR UPDATE cannot be applied to the nullable side of an outer join" beradi
        # (xuddi pay_debt dagi kabi, 82-qatorga qarang).
        sales = list(
            Sale.objects.select_for_update()
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
        if total_payment > total_debt:
            raise ValidationError(
                f"To'lov summasi umumiy qarzdan oshib ketdi. Qoldiq qarz: {total_debt:.2f}"
            )

        # Hovuzlar — chunklar nusxasi, taqsimot davomida kamayib boradi
        pools = [dict(c) for c in chunks]
        pool_idx = 0

        remaining = total_payment
        allocations = []
        for sale, sale_debt in debt_sales:
            if remaining <= 0:
                break
            alloc = min(sale_debt, remaining)

            # Shu sotuv uchun hovuzlardan chunklar yig'iladi
            sale_chunks = []
            need = alloc
            while need > 0 and pool_idx < len(pools):
                pool = pools[pool_idx]
                take = min(pool["amount"], need)
                if take > 0:
                    sale_chunks.append({
                        "type": pool["type"],
                        "amount": take,
                        "bank_card": pool.get("bank_card"),
                    })
                    pool["amount"] -= take
                    need -= take
                if pool["amount"] <= 0:
                    pool_idx += 1

            created = DebtService._apply_sale_payment(
                sale=sale,
                sale_debt=sale_debt,
                chunks=sale_chunks,
            )
            allocations.append({
                "sale": sale.id,
                "payment_id": created[0].id,
                "payment_ids": [p.id for p in created],
                "amount": f"{alloc:.2f}",
                "closed": alloc >= sale_debt,
                "sale_debt_left": f"{(sale_debt - alloc):.2f}",
            })
            remaining -= alloc

        return {
            "paid": f"{total_payment:.2f}",
            "remaining_debt": f"{(total_debt - total_payment):.2f}",
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
        """
        Mijozlar kesimida qarz balansi.

        INCREASE qatorlari qo'shiladi, DECREASE (to'lov) qatorlari ayiriladi —
        ilgari ikkalasi ham `Sum("amount")` bilan bir xil ishorada qo'shilardi,
        ya'ni har bir to'lov qarzni kamaytirish o'rniga oshirib ko'rsatardi.
        """
        qs = CustomerDebt.objects.all()

        if store_ids:
            qs = qs.filter(sale__store_id__in=store_ids)

        return qs.values("customer__full_name").annotate(
            debt=Sum(
                Case(
                    When(type=CustomerDebt.Type.INCREASE, then=F("amount")),
                    When(type=CustomerDebt.Type.DECREASE, then=-F("amount")),
                    default=Value(Decimal("0")),
                    output_field=DecimalField(max_digits=20, decimal_places=2),
                )
            )
        )
