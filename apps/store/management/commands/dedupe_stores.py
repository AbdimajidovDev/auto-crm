"""
Dublikat do'konlarni birlashtiruvchi BIR MARTALIK tuzatuv command.

Muammo: eski CRM migratsiyasi do'kon nomini Excel'dagi AYNI ko'rinishida
yaratgan (masalan "112 do`kon" — backtik), bazada esa oldindan "112 do'kon"
(apostrof) bo'lgan. Nom mos kelmagani uchun DUBLIKAT do'kon paydo bo'lgan:
butun import qilingan qoldiq/sotuv dublikat do'konga tushib qolgan, asl
do'kon (sotuvchilari bor) esa bo'sh qolgan.

Bu command apostrof/backtik farqini e'tiborsiz qoldirib, bir xil nomli
do'konlarni topadi va BITTA "kanonik" do'konga birlashtiradi:

  kanonik = eng ko'p StoreUser (sotuvchi/menejer) biriktirilgan do'kon
            (teng bo'lsa — eng kichik id).

Boshqa (dublikat) do'konlardagi BARCHA ma'lumot kanonikka ko'chiriladi,
so'ng dublikat do'konlar o'chiriladi.

DIQQAT: ma'lumotni o'zgartiradi va do'kon o'chiradi. Avval --dry-run bilan
ko'ring; hammasi transaction ichida.

Foydalanish:
    python manage.py dedupe_stores --dry-run
    python manage.py dedupe_stores
"""
from collections import defaultdict

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from apps.products.models import ProductBatch
from apps.store.models import Store, StoreUser

# Apostrof/backtik variantlari — solishtirishda bir xil hisoblanadi.
_QUOTE_TABLE = {ord(ch): "'" for ch in "`'’‘´ʼ"}


def store_key(name: str) -> str:
    return (name or "").strip().lower().translate(_QUOTE_TABLE)


class Command(BaseCommand):
    help = "Bir xil nomli (apostrof/backtik farqi) dublikat do'konlarni birlashtiradi."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Hech narsa o'zgartirmaydi, faqat rejani ko'rsatadi.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # 1) Bir xil (normallashtirilgan) nomli do'konlarni guruhlaymiz
        groups = defaultdict(list)
        for store in Store.objects.all():
            groups[store_key(store.name)].append(store)

        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
        if not dup_groups:
            self.stdout.write(self.style.SUCCESS("Dublikat do'kon topilmadi — tuzatish shart emas."))
            return

        # Har bir do'kon uchun StoreUser sonini oldindan hisoblaymiz (kanonik tanlash uchun)
        user_counts = {
            row["store"]: row["n"]
            for row in StoreUser.objects.values("store").annotate(n=Count("id"))
        }

        # 2) Store'ga ForeignKey bo'lgan barcha (model, field) larni avtomatik aniqlaymiz
        fk_fields = []  # [(model, field_name), ...]
        for model in apps.get_models():
            for f in model._meta.get_fields():
                if getattr(f, "related_model", None) is Store and f.many_to_one:
                    fk_fields.append((model, f.name))

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Dublikat guruhlar: {len(dup_groups)}" + (" [DRY-RUN]" if dry_run else "")
        ))

        try:
            with transaction.atomic():
                for key, stores in dup_groups.items():
                    canonical = self._pick_canonical(stores, user_counts)
                    dups = [s for s in stores if s.id != canonical.id]
                    self.stdout.write(
                        f"\n• '{canonical.name}' (id={canonical.id}) <- "
                        f"{', '.join(f'{repr(s.name)}(id={s.id})' for s in dups)}"
                    )
                    for dup in dups:
                        self._merge_store(dup, canonical, fk_fields, dry_run)

                if dry_run:
                    self.stdout.write(self.style.WARNING("\nDRY-RUN: o'zgarishlar bekor qilindi."))
                    transaction.set_rollback(True)
                else:
                    self.stdout.write(self.style.SUCCESS("\nBirlashtirish tugadi."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Xato (hammasi rollback): {e}"))
            raise

    @staticmethod
    def _pick_canonical(stores, user_counts):
        # Eng ko'p StoreUser, teng bo'lsa eng kichik id.
        return sorted(stores, key=lambda s: (-user_counts.get(s.id, 0), s.id))[0]

    def _merge_store(self, dup, canonical, fk_fields, dry_run):
        for model, field in fk_fields:
            qs = model.objects.filter(**{field: dup})
            n = qs.count()
            if not n:
                continue

            label = f"    {model._meta.label}.{field}: {n} ta -> id={canonical.id}"

            # ── Maxsus hollar (unique cheklov bor) ──────────────────────────
            if model is ProductBatch:
                moved, merged = self._merge_batches(dup, canonical, dry_run)
                self.stdout.write(f"    ProductBatch.{field}: ko'chirildi={moved}, qo'shildi={merged}")
                continue

            if model is StoreUser:
                moved, removed = self._merge_store_users(dup, canonical, dry_run)
                self.stdout.write(f"    StoreUser.{field}: ko'chirildi={moved}, o'chirildi(dublikat)={removed}")
                continue

            # ── Oddiy hol: to'g'ridan-to'g'ri qayta bog'lash ────────────────
            self.stdout.write(label)
            if not dry_run:
                qs.update(**{field: canonical})

        # Dublikat do'konni o'chiramiz (endi unga hech narsa bog'lanmagan)
        self.stdout.write(f"    -> do'kon o'chiriladi: id={dup.id} {repr(dup.name)}")
        if not dry_run:
            dup.delete()

    def _merge_batches(self, dup, canonical, dry_run):
        """(store,product) unikal — kanonikda allaqachon bor mahsulot bo'lsa, qoldiq qo'shiladi."""
        canon_by_product = {
            b.product_id: b for b in ProductBatch.objects.filter(store=canonical)
        }
        moved = merged = 0
        for batch in ProductBatch.objects.filter(store=dup):
            target = canon_by_product.get(batch.product_id)
            if target is None:
                moved += 1
                if not dry_run:
                    batch.store = canonical
                    batch.save(update_fields=["store"])
            else:
                merged += 1
                if not dry_run:
                    target.quantity = (target.quantity or 0) + (batch.quantity or 0)
                    target.save(update_fields=["quantity"])
                    batch.delete()
        return moved, merged

    def _merge_store_users(self, dup, canonical, dry_run):
        """(user, store) unikal — kanonikda allaqachon bor user bo'lsa, dublikat link o'chiriladi."""
        canon_users = set(
            StoreUser.objects.filter(store=canonical).values_list("user_id", flat=True)
        )
        moved = removed = 0
        for link in StoreUser.objects.filter(store=dup):
            if link.user_id in canon_users:
                removed += 1
                if not dry_run:
                    link.delete()
            else:
                moved += 1
                if not dry_run:
                    link.store = canonical
                    link.save(update_fields=["store"])
        return moved, removed
