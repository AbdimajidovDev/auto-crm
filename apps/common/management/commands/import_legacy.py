"""
Eski CRM ma'lumotlarini ("docs/" ichidagi Excel hisobotlari) bizning bazaga
ko'chiruvchi BIR MARTALIK management command.

Tahlil va mapping: docs/migratsiya-tahlili.md

Foydalanish:
    # Hammasi (FK tartibida): dicts -> products -> batches -> entries -> transfers -> sales -> writeoffs
    python manage.py import_legacy all

    # Faqat ayrim bosqichlar (vergul bilan yoki bo'sh joy bilan)
    python manage.py import_legacy dicts products batches

    # Hech narsa yozmasdan sinash (transaction rollback bilan — aniq hisob)
    python manage.py import_legacy all --dry-run

    # Boshqa papkadagi fayllar
    python manage.py import_legacy all --docs-dir /path/to/docs

DIQQAT:
  * Bu skript JORIY QOLDIQNI (ProductBatch) "остатки"dan oladi va kirim/sotuv/
    transfer/spisaniye ko'chirilganda qoldiqni QAYTA hisoblamaydi — faqat tarixiy
    hujjatlarni yozadi.
  * `dicts` va `products` qayta ishga tushirishga chidamli (get_or_create / mavjudni
    o'tkazib yuborish). `batches`, `entries`, `transfers`, `sales`, `writeoffs`
    HUJJAT yaratadi va idempotent EMAS — toza bazada yoki bir marta ishlating.
  * Tavsiya: avval `--dry-run`, so'ng PostgreSQL'da haqiqiy ishga tushirish.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.legacy import constants as C
from apps.common.legacy.importers import LegacyImporter


class _DryRunRollback(Exception):
    """--dry-run oxirida transaction'ni rollback qilish uchun ichki signal."""


class Command(BaseCommand):
    help = "Eski CRM Excel hisobotlarini ('docs/') bazaga ko'chiradi (migratsiya-tahlili.md)."

    def add_arguments(self, parser):
        parser.add_argument(
            "steps",
            nargs="*",
            default=["all"],
            help=f"Bosqich(lar): all yoki {', '.join(C.STEPS)}",
        )
        parser.add_argument(
            "--docs-dir",
            default=None,
            help="Excel fayllar papkasi (default: <BASE_DIR>/docs).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Hech narsa yozmaydi: yozadi va oxirida rollback qiladi (aniq hisob).",
        )

    def handle(self, *args, **options):
        docs_dir = Path(options["docs_dir"]) if options["docs_dir"] else Path(settings.BASE_DIR) / "docs"
        if not docs_dir.is_dir():
            raise CommandError(f"Papka topilmadi: {docs_dir}")

        steps = self._resolve_steps(options["steps"])
        dry_run = options["dry_run"]

        importer = LegacyImporter(docs_dir, stdout=self.stdout)
        runners = {
            "dicts":     importer.import_dicts,
            "products":  importer.import_products,
            "batches":   importer.import_batches,
            "entries":   importer.import_entries,
            "transfers": importer.import_transfers,
            "sales":     importer.import_sales,
            "writeoffs": importer.import_writeoffs,
        }

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Migratsiya: {', '.join(steps)}" + (" [DRY-RUN]" if dry_run else "")
        ))

        if dry_run:
            # Hamma bosqichni bitta transaction'da bajarib, oxirida bekor qilamiz —
            # shunda bosqichlararo bog'liqliklar (FK) ham realistik hisoblanadi.
            try:
                with transaction.atomic():
                    for step in steps:
                        runners[step]()
                    raise _DryRunRollback()
            except _DryRunRollback:
                self._summary(importer)
                self.stdout.write(self.style.WARNING("DRY-RUN: barcha o'zgarishlar bekor qilindi."))
            return

        # Haqiqiy yozuv: har bosqich o'zining atomic bloki bilan (importer ichida).
        for step in steps:
            runners[step]()

        self._summary(importer)
        self.stdout.write(self.style.SUCCESS("Migratsiya tugadi."))

    def _summary(self, importer):
        on_demand = importer.products.created_on_demand
        if on_demand:
            self.stdout.write(
                f"  ℹ 'остатки'da yo'q, lekin boshqa fayllarda uchragan {on_demand} "
                f"mahsulot qo'shimcha yaratildi."
            )

    @staticmethod
    def _resolve_steps(raw_steps):
        # Vergul bilan ham, bo'sh joy bilan ham qabul qilamiz: "dicts,products" yoki "dicts products"
        tokens = []
        for s in raw_steps:
            tokens += [t for t in s.split(",") if t]

        if not tokens or tokens == ["all"]:
            return list(C.STEPS)

        unknown = [t for t in tokens if t not in C.STEPS]
        if unknown:
            raise CommandError(
                f"Noma'lum bosqich(lar): {', '.join(unknown)}. "
                f"Ruxsat etilgan: all, {', '.join(C.STEPS)}"
            )
        # Foydalanuvchi tartibidan qat'i nazar, FK tartibida bajaramiz.
        return [s for s in C.STEPS if s in tokens]
