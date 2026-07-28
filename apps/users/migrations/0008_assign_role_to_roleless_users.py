from django.db import migrations

# RBAC ilgari fail-open edi: `user_permissions()` rolsiz userga `None`
# (cheklanmagan) qaytarardi, ya'ni rol biriktirilmagan sotuvchi butun tizimga
# ruxsat olardi. Endi rolsiz user hech narsa qila olmaydi.
#
# Bu migratsiya mavjud rolsiz (superuser bo'lmagan) userlarni eng cheklangan
# "Sotuvchi" roliga o'tkazadi — shunda tuzatish hech kimni tizimdan
# quvib chiqarmaydi. Admin keyin Rollar sahifasida to'g'rilashi mumkin.

FALLBACK_ROLE_NAME = "Sotuvchi"


def assign_default_role(apps, schema_editor):
    User = apps.get_model("users", "User")
    Role = apps.get_model("users", "Role")

    role = Role.objects.filter(name=FALLBACK_ROLE_NAME).first()
    if role is None:
        # 0006 seed o'chirilgan bo'lsa — ruxsatsiz bo'sh rol yaratamiz,
        # chunki rolsiz qoldirish endi to'liq bloklanish degani.
        role = Role.objects.create(
            name=FALLBACK_ROLE_NAME,
            description="Rolsiz userlar uchun zaxira rol",
            permissions=[],
        )

    User.objects.filter(role__isnull=True, is_superuser=False).update(role=role)


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0007_auditlog"),
    ]

    operations = [
        migrations.RunPython(assign_default_role, migrations.RunPython.noop),
    ]
