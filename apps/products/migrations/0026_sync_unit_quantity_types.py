from django.db import migrations


def sync_unit_quantity_types(apps, schema_editor):
    ProductUnitMeasurement = apps.get_model('products', 'ProductUnitMeasurement')
    Product = apps.get_model('products', 'Product')

    for unit in ProductUnitMeasurement.objects.all():
        name = (unit.measurement or "").lower()
        if any(k in name for k in ["пара", "para", "juft", "pair"]):
            unit.quantity_type = "QUARTER"
            unit.save(update_fields=["quantity_type"])

    for prod in Product.objects.filter(is_pair=True, unit_measurement__isnull=False):
        unit = prod.unit_measurement
        if unit.quantity_type != "QUARTER":
            unit.quantity_type = "QUARTER"
            unit.save(update_fields=["quantity_type"])


def reverse_sync(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0025_productunitmeasurement_quantity_type'),
    ]

    operations = [
        migrations.RunPython(sync_unit_quantity_types, reverse_sync),
    ]
