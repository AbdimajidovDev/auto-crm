# Maxsus (CRUD'dan tashqari) amal kodlari kiritilishi bo'yicha backfill.
#
# Yangi kodlar (permissions.py katalogiga qo'shildi):
#   sales.return, sales.archive, stockentry.import, stockentry.return,
#   stockentry.pay, transfers.approve, inventory.adjust, inventory.finalize,
#   inventory.cancel, products.import, debts.pay
#
# Ilgari bu amallar middleware'da metod bo'yicha umumiy kodga tushardi
# (masalan, POST /api/inventory/finalize/ -> inventory.create). RBAC fail-closed
# bo'lgani uchun yangi kod talab qilina boshlaganda mavjud rollar bu amallardan
# JIMGINA mahrum bo'lib qolardi. Shu migratsiya avvalgi xatti-harakatni saqlaydi:
# rolda "ota" kod bo'lsa, mos yangi kodlar qo'shiladi.
#
# debts moduli: endpointlari faqat to'lov bo'lgani uchun katalog view/pay ga
# qisqartirildi — eski debts.create/edit/delete kodlari pay ga almashtiriladi
# (serializer katalogda yo'q kodni rad etadi, shuning uchun tozalash shart).
from django.db import migrations

# ota kod -> shu kod bor rollarga qo'shiladigan yangi kodlar
GRANT_MAP = {
    "sales.create": ["sales.return"],
    "sales.delete": ["sales.archive"],
    "stockentry.create": ["stockentry.import", "stockentry.return", "stockentry.pay"],
    "transfers.create": ["transfers.approve"],
    "inventory.create": ["inventory.adjust", "inventory.finalize", "inventory.cancel"],
    "products.create": ["products.import"],
    "debts.create": ["debts.pay"],
}

# Katalogdan chiqarilgan kodlar — rollardan olib tashlanadi
REMOVED_CODES = {"debts.create", "debts.edit", "debts.delete"}


def grant_special_permissions(apps, schema_editor):
    Role = apps.get_model("users", "Role")
    for role in Role.objects.all():
        perms = list(role.permissions or [])
        current = set(perms)

        additions = []
        for parent, children in GRANT_MAP.items():
            if parent in current:
                additions.extend(c for c in children if c not in current)

        cleaned = [p for p in perms if p not in REMOVED_CODES]
        removed_any = len(cleaned) != len(perms)

        if additions or removed_any:
            role.permissions = cleaned + additions
            role.save(update_fields=["permissions"])


def revert_special_permissions(apps, schema_editor):
    """Teskari yo'nalish: yangi kodlarni olib tashlab, debts.pay ni create ga qaytaradi."""
    Role = apps.get_model("users", "Role")
    new_codes = {c for children in GRANT_MAP.values() for c in children}
    for role in Role.objects.all():
        perms = list(role.permissions or [])
        had_pay = "debts.pay" in perms
        cleaned = [p for p in perms if p not in new_codes]
        if had_pay and "debts.create" not in cleaned:
            cleaned.append("debts.create")
        if cleaned != perms:
            role.permissions = cleaned
            role.save(update_fields=["permissions"])


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0008_assign_role_to_roleless_users"),
    ]

    operations = [
        migrations.RunPython(grant_special_permissions, revert_special_permissions),
    ]
