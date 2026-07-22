"""
Eski to'lovlar orasidan QARZ to'lovlarini belgilaydi.

Belgi: sotuv yaratilishidan 60+ soniya KEYIN yozilgan to'lov (refund emas) —
qarz to'lovi hisoblanadi. Sotuv paytidagi to'lovlar sotuv bilan bitta
tranzaksiyada (bir necha ms ichida) yoziladi, shuning uchun bu chegara ishonchli.
"""
from datetime import timedelta

from django.db import migrations
from django.db.models import F


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0020_payment_is_debt_payment"),
    ]

    operations = [
        migrations.RunPython(
            lambda apps, schema_editor: apps.get_model("sales", "Payment").objects.filter(
                is_refund=False,
                sale__isnull=False,
                created_at__gt=F("sale__created_at") + timedelta(seconds=60),
            ).update(is_debt_payment=True),
            migrations.RunPython.noop,
        ),
    ]
