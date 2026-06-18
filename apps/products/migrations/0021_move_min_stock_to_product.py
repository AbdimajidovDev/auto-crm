from django.db import migrations, models
from django.db.models import Max


def copy_min_stock_to_product(apps, schema_editor):
    """Preserve existing thresholds: Product.min_stock = max(batch.min_stock)."""
    Product = apps.get_model("products", "Product")
    ProductBatch = apps.get_model("products", "ProductBatch")

    rows = (
        ProductBatch.objects
        .values("product_id")
        .annotate(threshold=Max("min_stock"))
        .filter(threshold__gt=0)
    )
    for row in rows:
        Product.objects.filter(id=row["product_id"]).update(
            min_stock=row["threshold"]
        )


def noop_reverse(apps, schema_editor):
    # On reverse, the per-product value is copied back to every batch below.
    pass


def copy_min_stock_to_batches(apps, schema_editor):
    """Reverse data migration: push the product threshold back onto its batches."""
    ProductBatch = apps.get_model("products", "ProductBatch")
    for batch in ProductBatch.objects.select_related("product").iterator():
        ProductBatch.objects.filter(id=batch.id).update(
            min_stock=batch.product.min_stock
        )


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0020_productbatch_min_stock'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='min_stock',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(copy_min_stock_to_product, copy_min_stock_to_batches),
        migrations.RemoveField(
            model_name='productbatch',
            name='min_stock',
        ),
    ]