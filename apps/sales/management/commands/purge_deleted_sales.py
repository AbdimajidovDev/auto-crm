from django.core.management.base import BaseCommand

from apps.sales.views.sale_view import purge_expired_deleted_sales, SALE_ARCHIVE_RETENTION_DAYS


class Command(BaseCommand):
    help = (
        f"Arxivda {SALE_ARCHIVE_RETENTION_DAYS} kundan ortiq turgan (soft-delete) "
        "sotuvlarni butunlay o'chiradi. Cron uchun; arxiv API'lari ham har "
        "so'rovda shu purge'ni chaqiradi, bu buyruq qo'shimcha kafolat."
    )

    def handle(self, *args, **options):
        purged = purge_expired_deleted_sales()
        self.stdout.write(self.style.SUCCESS(f"Butunlay o'chirildi: {purged} ta sotuv"))
