"""
RBAC permission katalogi va yordamchi funksiyalar.

Permission kodi formati: "<modul>.<amal>", masalan "products.create".
Rolga shu kodlar ro'yxati biriktiriladi (Role.permissions JSON).

Qoidalar:
  - superuser — hamma narsaga ruxsat (rol shart emas);
  - roli YO'Q user — cheklanmagan (eski userlar buzilmasligi uchun);
  - roli BOR user — faqat roldagi kodlarga ruxsat.
"""

ACTION_LABELS = {
    "view": "Ko'rish",
    "create": "Qo'shish",
    "edit": "Tahrirlash",
    "delete": "O'chirish",
    "archive": "Arxivlash",
}

# Modul -> ruxsat etiladigan amallar. Frontend menyulari va API prefikslari
# shu modullarga bog'lanadi (core/middleware/rbac.py dagi PATH_MODULE_MAP).
PERMISSION_CATALOG = [
    {"module": "dashboard",  "label": "Boshqaruv paneli",     "actions": ["view"]},
    {"module": "products",   "label": "Mahsulotlar",          "actions": ["view", "create", "edit", "delete", "archive"]},
    {"module": "categories", "label": "Kategoriyalar",        "actions": ["view", "create", "edit", "delete"]},
    {"module": "sales",      "label": "Sotuvlar",             "actions": ["view", "create", "edit", "delete"]},
    {"module": "inventory",  "label": "Inventarizatsiya",     "actions": ["view", "create", "edit"]},
    {"module": "stockentry", "label": "Xarid (kirim)",        "actions": ["view", "create", "edit", "delete"]},
    {"module": "transfers",  "label": "Transferlar",          "actions": ["view", "create", "edit", "delete"]},
    {"module": "writeoff",   "label": "Spisaniye",            "actions": ["view", "create", "edit", "delete"]},
    {"module": "customers",  "label": "Mijozlar",             "actions": ["view", "create", "edit", "delete"]},
    {"module": "debts",      "label": "Qarzlar",              "actions": ["view", "create", "edit", "delete"]},
    {"module": "suppliers",  "label": "Yetkazib beruvchilar", "actions": ["view", "create", "edit", "delete"]},
    {"module": "stores",     "label": "Do'konlar",            "actions": ["view", "create", "edit", "delete"]},
    {"module": "reports",    "label": "Hisobotlar",           "actions": ["view"]},
    {"module": "users",      "label": "Foydalanuvchilar",     "actions": ["view", "create", "edit", "delete"]},
    {"module": "roles",      "label": "Rollar",               "actions": ["view", "create", "edit", "delete"]},
    {"module": "audit",      "label": "Amallar jurnali",      "actions": ["view"]},
]

ALL_PERMISSION_CODES = frozenset(
    f"{entry['module']}.{action}"
    for entry in PERMISSION_CATALOG
    for action in entry["actions"]
)


def catalog_for_api():
    """Frontend'dagi checkbox-matritsa uchun katalog (kod + label bilan)."""
    return [
        {
            "module": entry["module"],
            "label": entry["label"],
            "actions": [
                {
                    "action": action,
                    "code": f"{entry['module']}.{action}",
                    "label": ACTION_LABELS.get(action, action),
                }
                for action in entry["actions"]
            ],
        }
        for entry in PERMISSION_CATALOG
    ]


def user_permissions(user):
    """
    Userning amaldagi permission'lari.
    None — cheklanmagan (superuser yoki rolsiz user).
    """
    if not getattr(user, "is_authenticated", False):
        return frozenset()
    if user.is_superuser or user.role_id is None:
        return None
    return frozenset(user.role.permissions or [])


def user_has_perm(user, code: str) -> bool:
    perms = user_permissions(user)
    return perms is None or code in perms
