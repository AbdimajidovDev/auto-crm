"""
barcode mavjud, lekin shtrix_code (barcode rasmi) bo'sh bo'lgan Product'lar uchun
rasm generatsiya qiluvchi management command.

Nima uchun kerak: migratsiya (import_legacy) mahsulotlarni `bulk_create` bilan
yaratadi — bu `Product.save()` ni chaqirmaydi, demak shtrix-rasm yaratilmaydi.
Bu command import tugagandan keyin yetishmayotgan rasmlarni to'ldiradi.

Idempotent: shtrix_code allaqachon bor mahsulotlar o'tkazib yuboriladi —
mavjud rasmlar QAYTA yaratilmaydi. Shuning uchun xohlagancha qayta ishlatish
mumkin.

Foydalanish:
    python manage.py generate_missing_barcodes
    python manage.py generate_missing_barcodes --dry-run        # faqat sanaydi
    python manage.py generate_missing_barcodes --limit 100      # test uchun
"""
from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.products.models import Product
from apps.products.utils.barcode_utility import generate_barcode_image


class Command(BaseCommand):
    help = "barcode bor, lekin shtrix_code bo'sh Product'lar uchun barcode rasmini yaratadi (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Rasm yaratmaydi, faqat nechta mahsulot borligini ko'rsatadi.")
        parser.add_argument("--limit", type=int, default=None,
                            help="Faqat shuncha mahsulotni qayta ishlash (test uchun).")
        parser.add_argument("--progress-every", type=int, default=200,
                            help="Har nechta mahsulotda progress chiqarish (default 200).")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]
        progress_every = max(options["progress_every"], 1)

        # barcode BOR (bo'sh emas) VA shtrix_code BO'SH (null yoki '') bo'lganlar.
        qs = (
            Product.objects
            .exclude(barcode__isnull=True)
            .exclude(barcode="")
            .filter(Q(shtrix_code__isnull=True) | Q(shtrix_code=""))
            .order_by("id")
        )
        if limit:
            qs = qs[:limit]

        total = qs.count()
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"shtrix-rasm yaratiladigan mahsulotlar: {total}" + (" [DRY-RUN]" if dry_run else "")
        ))
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Hammasida shtrix-rasm bor — qiladigan ish yo'q."))
            return
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN: rasm yaratilmadi."))
            return

        created = 0
        skipped = 0
        errors = 0
        error_samples = []

        # .iterator() — minglab mahsulotda xotirani tejaydi.
        for i, product in enumerate(qs.iterator(chunk_size=500), start=1):
            # Idempotentlik kafolati: oraliqda rasm paydo bo'lgan bo'lsa — o'tkazamiz.
            if product.shtrix_code:
                skipped += 1
            else:
                try:
                    product.shtrix_code.save(
                        f"{product.barcode}.png",
                        generate_barcode_image(product.barcode),
                        save=False,
                    )
                    product.save(update_fields=["shtrix_code"])
                    created += 1
                except Exception as e:
                    errors += 1
                    if len(error_samples) < 10:
                        error_samples.append(f"id={product.id} barcode={product.barcode!r}: {e}")

            if i % progress_every == 0 or i == total:
                self.stdout.write(
                    f"  {i}/{total} (yaratildi={created}, o'tkazildi={skipped}, xato={errors})"
                )

        # ── Yakuniy statistika ────────────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Tugadi — created={created}, skipped={skipped}, errors={errors} (jami {total})"
        ))
        if error_samples:
            self.stdout.write(self.style.ERROR("Xato namunalari (eng ko'pi 10 ta):"))
            for s in error_samples:
                self.stdout.write(f"  - {s}")
