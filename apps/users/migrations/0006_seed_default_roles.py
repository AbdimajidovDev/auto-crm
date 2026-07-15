from django.db import migrations

# Eski statik do'kon rollari (Sotuvchi 's' / Menejer 'm') vazifalarini to'liq
# qoplaydigan tayyor tizim rollari. Admin ularni Rollar sahifasida erkin
# tahrirlashi mumkin — migratsiya faqat yo'q bo'lsa yaratadi (idempotent).

SELLER_PERMISSIONS = [
    "dashboard.view",
    "sales.view", "sales.create",
    "transfers.view", "transfers.create",
    "products.view",
    "categories.view",
    "customers.view", "customers.create", "customers.edit",
    "debts.view", "debts.create",
]

MANAGER_PERMISSIONS = SELLER_PERMISSIONS + [
    "inventory.view", "inventory.create", "inventory.edit",
    "stockentry.view",
    "products.create", "products.edit", "products.archive",
    "categories.create", "categories.edit",
    "writeoff.view", "writeoff.create",
    "sales.edit",
    "debts.edit",
]

DEFAULT_ROLES = [
    ("Sotuvchi", "Kassa/sotuv bilan ishlaydi (eski statik 'Sotuvchi' roli o'rnida)", SELLER_PERMISSIONS),
    ("Menejer", "Sotuv + inventarizatsiya va mahsulot boshqaruvi (eski statik 'Menejyer' roli o'rnida)", MANAGER_PERMISSIONS),
]


def seed_roles(apps, schema_editor):
    Role = apps.get_model("users", "Role")
    for name, description, permissions in DEFAULT_ROLES:
        Role.objects.get_or_create(
            name=name,
            defaults={"description": description, "permissions": permissions},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0005_role_user_role"),
    ]

    operations = [
        migrations.RunPython(seed_roles, migrations.RunPython.noop),
    ]
