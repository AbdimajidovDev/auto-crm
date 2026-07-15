from django.db import migrations
from django.db.models import Q


def _category_prefix(category) -> str:
    # Product.get_category_prefix() bilan bir xil mantiq (historical modelda
    # metodlar bo'lmagani uchun shu yerda takrorlanadi).
    name = (getattr(category, "name_uz", None) or "").strip() if category else ""
    if not name:
        return "PRD"
    prefix = "".join(word[0].upper() for word in name.split() if word)
    return prefix or "PRD"


def backfill_missing_sku(apps, schema_editor):
    """
    sku'siz mahsulotlarga Product.generate_sku() formatida sku beradi.

    Bunday yozuvlar bulk_create orqali kirgan (legacy/Excel import) — u
    Product.save() dagi avtogeneratsiyani chetlab o'tadi va API'da sku=null
    bo'lib qoladi.
    """
    Product = apps.get_model("products", "Product")

    taken = set(
        Product.objects.exclude(sku__isnull=True).exclude(sku="").values_list("sku", flat=True)
    )

    to_update = []
    missing = (
        Product.objects
        .select_related("category")
        .filter(Q(sku__isnull=True) | Q(sku=""))
        .only("id", "sku", "category__name_uz")
    )
    for product in missing.iterator():
        base = f"{_category_prefix(product.category)}-{product.id:06d}"
        sku, n = base, 1
        while sku in taken:
            sku = f"{base}-{n}"
            n += 1
        taken.add(sku)
        product.sku = sku
        to_update.append(product)

    if to_update:
        Product.objects.bulk_update(to_update, ["sku"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0022_productbatch_uniq_product_batch_store_product"),
    ]

    operations = [
        migrations.RunPython(backfill_missing_sku, migrations.RunPython.noop),
    ]
