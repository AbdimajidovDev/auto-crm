from django.core.management.base import BaseCommand
from django.db import transaction

from apps.sales.models import SaleItem
from apps.products.models import ProductBatch


"""
    SalesItem madeliga yangi purchase field qo'shilgani uchun bu funksiya yozildi
    avtomatik to'ldirib beradi. faqat bir martta ishlatish kifoya.
"""


class Command(BaseCommand):
    help = "Fill purchase_price for old SaleItem records"

    @transaction.atomic
    def handle(self, *args, **kwargs):

        items = SaleItem.objects.filter(purchase_price__isnull=True)

        self.stdout.write(f"Topildi: {items.count()} ta item")

        updated = 0

        for item in items.select_related("sale", "product"):

            batch = ProductBatch.objects.filter(
                product=item.product,
                store=item.sale.store
            ).first()

            if not batch:
                continue

            item.purchase_price = batch.purchase_price
            item.save(update_fields=["purchase_price"])

            updated += 1

        self.stdout.write(self.style.SUCCESS(f"{updated} ta yangilandi"))


"""
    python manage.py fill_purchase_price
"""