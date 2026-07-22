"""
Eski qaytarimlarga payment_group bog'laydi: qaytarim bilan bir xil sotuvga,
qaytarim yaratilgan vaqtdan ±2 soniya ichida yozilgan refund Payment qatorlari
shu qaytarimning pul qaytarish harakati hisoblanadi (bitta tranzaksiyada
ketma-ket yozilgani uchun ishonchli belgi).
"""
from datetime import timedelta

from django.db import migrations


WINDOW = timedelta(seconds=2)


def backfill(apps, schema_editor):
    SaleReturn = apps.get_model("sales", "SaleReturn")
    Payment = apps.get_model("sales", "Payment")

    to_update = []
    returns = SaleReturn.objects.filter(payment_group__isnull=True).only(
        "id", "sale_id", "created_at"
    )
    for ret in returns.iterator(chunk_size=500):
        groups = list(
            Payment.objects
            .filter(
                sale_id=ret.sale_id,
                is_refund=True,
                payment_group__isnull=False,
                created_at__gte=ret.created_at - WINDOW,
                created_at__lte=ret.created_at + WINDOW,
            )
            .values_list("payment_group", flat=True)
            .distinct()
        )
        # Faqat bitta aniq guruh topilsa bog'laymiz — ikkilanish bo'lsa tegmaymiz
        if len(groups) == 1:
            ret.payment_group = groups[0]
            to_update.append(ret)

    if to_update:
        SaleReturn.objects.bulk_update(to_update, ["payment_group"], batch_size=500)


def reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0018_salereturn_payment_group"),
    ]

    operations = [
        migrations.RunPython(backfill, reverse),
    ]
