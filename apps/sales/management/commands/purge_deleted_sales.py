from django.core.management.base import BaseCommand

from apps.sales.views.sale_view import purge_expired_deleted_sales, SALE_ARCHIVE_RETENTION_DAYS


class Command(BaseCommand):
    help = (
        "Arxivdagi sotuvlarni tozalash buyrug'i (Indefinite Retention siyosati). "
        "Tarixiy buxgalteriya, FIFO va StockAllocation daxlsizligini saqlash uchun "
        "sotuvlar bazadan fizik o'chirilmaydi, balki soft-delete arxivda saqlanadi."
    )

    def handle(self, *args, **options):
        purged = purge_expired_deleted_sales()
        self.stdout.write(
            self.style.SUCCESS(
                "Indefinite Retention faol: sotuvlar va ombor harakatlari arxivda daxlsiz saqlanadi. "
                f"Fizik o'chirilgan: {purged} ta sotuv."
            )
        )
