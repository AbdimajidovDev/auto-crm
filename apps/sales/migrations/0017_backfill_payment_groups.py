"""
payment_group qo'shilishidan OLDIN yaratilgan split to'lovlarni guruhlaydi.

Bitta to'lov harakatining qatorlari bitta tranzaksiyada ketma-ket yoziladi —
shuning uchun bir xil sale/customer/is_refund va 2 soniya ichidagi qatorlar
bitta harakat deb hisoblanadi va bitta payment_group oladi.
Faqat 2+ qatorli klasterlar belgilanadi (yakka to'lovlarga guruh shart emas).
"""
import uuid

from django.db import migrations


CLUSTER_WINDOW_SECONDS = 2


def backfill_groups(apps, schema_editor):
    Payment = apps.get_model("sales", "Payment")

    rows = list(
        Payment.objects
        .filter(payment_group__isnull=True)
        .order_by("sale_id", "customer_id", "is_refund", "created_at", "id")
        .only("id", "sale_id", "customer_id", "is_refund", "created_at")
    )

    to_update = []
    cluster = []

    def flush(cluster_rows):
        if len(cluster_rows) < 2:
            return
        group = uuid.uuid4()
        for r in cluster_rows:
            r.payment_group = group
            to_update.append(r)

    prev = None
    for row in rows:
        same_event = (
            prev is not None
            and row.sale_id == prev.sale_id
            and row.customer_id == prev.customer_id
            and row.is_refund == prev.is_refund
            and (row.created_at - prev.created_at).total_seconds() <= CLUSTER_WINDOW_SECONDS
        )
        if same_event:
            cluster.append(row)
        else:
            flush(cluster)
            cluster = [row]
        prev = row
    flush(cluster)

    if to_update:
        Payment.objects.bulk_update(to_update, ["payment_group"], batch_size=1000)


def reverse_groups(apps, schema_editor):
    # Backfill'ni bekor qilishning xavfsiz yo'li yo'q (qaysi guruh backfill'dan,
    # qaysi servisdan kelganini ajratib bo'lmaydi) — hech narsa qilinmaydi
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0016_payment_payment_group"),
    ]

    operations = [
        migrations.RunPython(backfill_groups, reverse_groups),
    ]
