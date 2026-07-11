"""
Eski sotuvlarning payment_type qiymatini mavjud to'lovlardan hisoblab to'ldiradi.

Qoida runtime bilan BIR XIL — apps.sales.payment_rules.compute_payment_type
(pure funksiya, ORM/modellarga bog'liq emas, shuning uchun migratsiyada
import qilish xavfsiz). To'lovi yo'q sotuvlar DEBT bo'lib qoladi (field default).

LEGACY istisno: Excel importidan kelgan sotuvlarda Payment yozuvlari umuman yo'q,
lekin paid_amount > 0 va status=paid. Ularni DEBT deb belgilash hisobotni buzadi —
bunday sotuvlar NAQD (CASH) deb qabul qilinadi (o'sha davrda karta hisobi yuritilmagan).

Eslatma: bu migratsiya paytida barcha mavjud Payment yozuvlari is_refund=False
(maydon endigina qo'shildi), shuning uchun NET = oddiy SUM.
"""

from decimal import Decimal

from django.db import migrations
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from apps.sales.payment_rules import compute_payment_type

BATCH_SIZE = 1000


def backfill_payment_type(apps, schema_editor):
    Sale = apps.get_model("sales", "Sale")

    zero = Value(Decimal("0"), output_field=DecimalField())

    qs = (
        Sale.objects
        .annotate(
            payment_count=Count("payments"),
            cash_total=Coalesce(Sum("payments__amount", filter=Q(payments__type="cash")), zero),
            card_total=Coalesce(Sum("payments__amount", filter=Q(payments__type="card")), zero),
        )
        .only("id", "payment_type", "paid_amount")
    )

    batch = []
    for sale in qs.iterator(chunk_size=BATCH_SIZE):
        if sale.payment_count == 0 and sale.paid_amount and sale.paid_amount > 0:
            # Legacy (Excel import) sotuv: to'lov yozuvi yo'q, lekin pul olingan → naqd
            sale.payment_type = "cash"
        else:
            sale.payment_type = compute_payment_type(sale.cash_total, sale.card_total)
        batch.append(sale)

        if len(batch) >= BATCH_SIZE:
            Sale.objects.bulk_update(batch, ["payment_type"], batch_size=BATCH_SIZE)
            batch = []

    if batch:
        Sale.objects.bulk_update(batch, ["payment_type"], batch_size=BATCH_SIZE)


def reverse_noop(apps, schema_editor):
    # Orqaga qaytishda hech narsa qilinmaydi — maydon 0011 da olib tashlanadi
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0011_payment_is_refund_sale_payment_type_bankcard_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_payment_type, reverse_noop),
    ]
