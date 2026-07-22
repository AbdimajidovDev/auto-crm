"""
Mavjud kirimlarning yassi cash_amount/card_amount/bank_card qiymatlarini
StockEntryPayment split qatorlariga ko'chiradi — statistika (chiqimlar,
karta kesimlari) tarixiy davrlar uchun ham to'liq chiqishi uchun.

created_at/updated_at entry sanasiga tenglashtiriladi (auto_now_add ni
bulk_create chetlab o'ta olmaydi — keyin bitta UPDATE bilan to'g'rilanadi).
"""
from django.db import migrations


def backfill_payments(apps, schema_editor):
    StockEntry = apps.get_model("contract", "StockEntry")
    StockEntryPayment = apps.get_model("contract", "StockEntryPayment")

    rows = []
    entries = StockEntry.objects.filter().only(
        "id", "cash_amount", "card_amount", "bank_card_id"
    )
    for entry in entries.iterator(chunk_size=1000):
        if entry.cash_amount and entry.cash_amount > 0:
            rows.append(StockEntryPayment(
                entry_id=entry.id,
                amount=entry.cash_amount,
                type="cash",
                bank_card_id=None,
            ))
        if entry.card_amount and entry.card_amount > 0:
            rows.append(StockEntryPayment(
                entry_id=entry.id,
                amount=entry.card_amount,
                type="card",
                # Eski yozuvlarda karta ko'rsatilmagan bo'lishi mumkin —
                # NULL qoladi, hisobotda "Noma'lum karta" bo'lib chiqadi
                bank_card_id=entry.bank_card_id,
            ))

    if rows:
        StockEntryPayment.objects.bulk_create(rows, batch_size=1000)
        # created_at ni entry sanasiga tenglashtirish (davr filtrlari uchun)
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE stock_entry_payment sep
                SET created_at = se.created_at,
                    updated_at = se.updated_at
                FROM stock_entry se
                WHERE sep.entry_id = se.id
                """
            )


def reverse_backfill(apps, schema_editor):
    StockEntryPayment = apps.get_model("contract", "StockEntryPayment")
    StockEntryPayment.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("contract", "0012_purchasesession_payments_stockentrypayment"),
    ]

    operations = [
        migrations.RunPython(backfill_payments, reverse_backfill),
    ]
