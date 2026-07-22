"""
payment_group qo'shilishidan OLDIN yaratilgan ta'minotchi split to'lovlarini
guruhlaydi: bir xil entry, type='pay' va 2 soniya ichidagi qatorlar bitta
to'lov harakati deb hisoblanadi. Faqat 2+ qatorli klasterlar belgilanadi.
"""
import uuid

from django.db import migrations


CLUSTER_WINDOW_SECONDS = 2


def backfill_groups(apps, schema_editor):
    SupplierTransaction = apps.get_model("contract", "SupplierTransaction")

    rows = list(
        SupplierTransaction.objects
        .filter(payment_group__isnull=True, type="pay")
        .order_by("entry_id", "created_at", "id")
        .only("id", "entry_id", "created_at")
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
            and row.entry_id == prev.entry_id
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
        SupplierTransaction.objects.bulk_update(to_update, ["payment_group"], batch_size=1000)


def reverse_groups(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("contract", "0014_suppliertransaction_payment_group"),
    ]

    operations = [
        migrations.RunPython(backfill_groups, reverse_groups),
    ]
