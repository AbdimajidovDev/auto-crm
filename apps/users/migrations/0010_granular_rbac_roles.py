from django.db import migrations

GRANULAR_MAP = {
    "products.view": [
        "products.stock.view",
        "products.history.view",
        "categories.view",
        "brands.view",
        "units.view",
        "locations.view",
    ],
    "products.create": ["products.import.create", "products.import.view"],
    "products.edit": ["products.stock.adjust", "products.stock.min_stock"],
    "sales.return": ["sales.return.view", "sales.return.create"],
    "sales.view": ["bank_cards.view"],
    "inventory.view": ["low_stock.view"],
    "inventory.create": ["low_stock.export"],
    "inventory.adjust": ["products.stock.adjust", "import.create", "writeoff.create", "import.view", "writeoff.view"],
    "writeoff.create": ["writeoff.view", "writeoff.cancel"],
    "stockentry.view": ["suppliers.view"],
    "stockentry.create": [
        "stockentry.session.create",
        "stockentry.session.confirm",
        "stockentry.import.create",
        "stockentry.import.view",
        "stockentry.return.create",
        "stockentry.return.view",
        "stockentry.pay.create",
        "stockentry.pay.view",
    ],
    "reports.view": [
        "reports.sales.view",
        "reports.sales.export",
        "reports.top_products.view",
        "reports.top_products.export",
        "reports.products.view",
        "reports.products.export",
        "reports.low_stock.view",
        "reports.low_stock.export",
        "reports.product_history.view",
        "reports.product_history.export",
        "reports.customers.view",
        "reports.customers.export",
        "reports.suppliers.view",
        "reports.suppliers.export",
        "reports.supplier_sales.view",
        "reports.supplier_sales.export",
        "reports.stock_leftovers.view",
        "reports.stock_leftovers.export",
        "reports.payments.view",
        "reports.payments.export",
        "reports.expenses.view",
        "reports.expenses.export",
    ],
}


def migrate_to_granular_permissions(apps, schema_editor):
    Role = apps.get_model("users", "Role")
    for role in Role.objects.all():
        perms = set(role.permissions or [])
        additions = set()
        for old_code, new_codes in GRANULAR_MAP.items():
            if old_code in perms:
                additions.update(new_codes)

        # Doimiy standart qo'shimchalar
        if "sales.create" in perms:
            additions.add("bank_cards.view")
            additions.add("customers.view")

        updated = sorted(list(perms | additions))
        if updated != list(perms):
            role.permissions = updated
            role.save(update_fields=["permissions"])


def rollback_granular_permissions(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0009_grant_special_action_permissions"),
    ]

    operations = [
        migrations.RunPython(migrate_to_granular_permissions, rollback_granular_permissions),
    ]
