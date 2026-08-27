"""
AutoCRM RBAC — Granular Permission Katalogi va Yordamchi Funksiyalar.

Permission formati: "<module>.<action>" yoki "<module>.<feature>.<action>"
Ierarxiya: MODUL -> SUBMODUL / XUSUSIYAT -> AMAL

Qoidalar:
  - Superuser (is_superuser=True) — hamma narsaga ruxsat (bypass);
  - Roli yo'q user — hech qanday ruxsat yo'q (fail-closed);
  - Roli bor user — faqat roldagi aniq permission kodlariga ruxsat;
  - Hisobotlar (reports) har biri alohida ko'rish (view) va yuklab olish (export) huquqlariga ega.
"""

from __future__ import annotations
from typing import Any
from rest_framework.permissions import BasePermission

# ─────────────────────────────────────────────────────────────────────────────
# 1. HIERARCHICAL PERMISSION MATRIX
# ─────────────────────────────────────────────────────────────────────────────

PERMISSION_HIERARCHY = [
    {
        "module": "dashboard",
        "label": "Boshqaruv paneli",
        "features": [
            {
                "feature": "dashboard",
                "label": "Asosiy ko'rsatkichlar",
                "actions": [
                    {"code": "dashboard.view", "action": "view", "label": "Boshqaruv panelini ko'rish"},
                ],
            }
        ],
    },
    {
        "module": "products",
        "label": "Mahsulotlar",
        "features": [
            {
                "feature": "catalog",
                "label": "Mahsulotlar katalogi",
                "actions": [
                    {"code": "products.view", "action": "view", "label": "Mahsulotlarni ko'rish"},
                    {"code": "products.create", "action": "create", "label": "Mahsulot qo'shish"},
                    {"code": "products.edit", "action": "edit", "label": "Mahsulotni tahrirlash"},
                    {"code": "products.delete", "action": "delete", "label": "Mahsulotni o'chirish"},
                    {"code": "products.archive", "action": "archive", "label": "Arxivlash / Status o'zgartirish"},
                    {"code": "products.export", "action": "export", "label": "Eksport (Excel)"},
                ],
            },
            {
                "feature": "stock",
                "label": "Qoldiqlar va Narxlar",
                "actions": [
                    {"code": "products.stock.view", "action": "view", "label": "Do'konlar qoldiqlarini ko'rish"},
                    {"code": "products.stock.adjust", "action": "adjust", "label": "Qoldiqni qo'lda to'g'irlash"},
                    {"code": "products.stock.min_stock", "action": "min_stock", "label": "Minimal qoldiq (MinStock) o'zgartirish"},
                ],
            },
            {
                "feature": "history",
                "label": "Mahsulot tarixi",
                "actions": [
                    {"code": "products.history.view", "action": "view", "label": "Mahsulot tarixini ko'rish"},
                ],
            },
            {
                "feature": "excel_import",
                "label": "Excel orqali import",
                "actions": [
                    {"code": "products.import.view", "action": "view", "label": "Import shablonini ko'rish"},
                    {"code": "products.import.create", "action": "create", "label": "Exceldan mahsulotlarni import qilish"},
                ],
            },
            {
                "feature": "categories",
                "label": "Kategoriyalar",
                "actions": [
                    {"code": "categories.view", "action": "view", "label": "Kategoriyalarni ko'rish"},
                    {"code": "categories.create", "action": "create", "label": "Kategoriya qo'shish"},
                    {"code": "categories.edit", "action": "edit", "label": "Kategoriyani tahrirlash"},
                    {"code": "categories.delete", "action": "delete", "label": "Kategoriyani o'chirish"},
                    {"code": "categories.export", "action": "export", "label": "Eksport (Excel)"},
                ],
            },
            {
                "feature": "attributes",
                "label": "Brend, birlik va joylashuv",
                "actions": [
                    {"code": "brands.view", "action": "view", "label": "Brendlarni ko'rish"},
                    {"code": "brands.create", "action": "create", "label": "Brend qo'shish"},
                    {"code": "brands.edit", "action": "edit", "label": "Brendni tahrirlash"},
                    {"code": "brands.delete", "action": "delete", "label": "Brendni o'chirish"},
                    {"code": "units.view", "action": "view", "label": "O'lchov birliklarini ko'rish"},
                    {"code": "units.create", "action": "create", "label": "O'lchov birligi qo'shish"},
                    {"code": "units.edit", "action": "edit", "label": "O'lchov birligini tahrirlash"},
                    {"code": "units.delete", "action": "delete", "label": "O'lchov birligini o'chirish"},
                    {"code": "locations.view", "action": "view", "label": "Joylashuvlarni ko'rish"},
                    {"code": "locations.create", "action": "create", "label": "Joylashuv qo'shish"},
                    {"code": "locations.edit", "action": "edit", "label": "Joylashuvni tahrirlash"},
                    {"code": "locations.delete", "action": "delete", "label": "Joylashuvni o'chirish"},
                ],
            },
        ],
    },
    {
        "module": "sales",
        "label": "Savdo / Sotuvlar",
        "features": [
            {
                "feature": "sales",
                "label": "Sotuvlar",
                "actions": [
                    {"code": "sales.view", "action": "view", "label": "Sotuvlar ro'yxatini ko'rish"},
                    {"code": "sales.create", "action": "create", "label": "Yangi sotuv yaratish (POS)"},
                    {"code": "sales.edit", "action": "edit", "label": "Sotuvni tahrirlash"},
                    {"code": "sales.cancel", "action": "cancel", "label": "Sotuvni bekor qilish / Arxivlash"},
                    {"code": "sales.delete", "action": "delete", "label": "Sotuvni o'chirish (Bulk delete)"},
                    {"code": "sales.export", "action": "export", "label": "Eksport (Excel)"},
                ],
            },
            {
                "feature": "archive",
                "label": "Sotuvlar arxivi",
                "actions": [
                    {"code": "sales.archive.view", "action": "view", "label": "Arxivlangan sotuvlarni ko'rish"},
                    {"code": "sales.archive.restore", "action": "restore", "label": "Arxivdan qayta tiklash"},
                ],
            },
            {
                "feature": "returns",
                "label": "Savdo qaytimlari (Vozvrat)",
                "actions": [
                    {"code": "sales.return.view", "action": "view", "label": "Qaytimlar ro'yxatini ko'rish"},
                    {"code": "sales.return.create", "action": "create", "label": "Mijozdan qaytim qabul qilish"},
                ],
            },
            {
                "feature": "bank_cards",
                "label": "Bank kartalari",
                "actions": [
                    {"code": "bank_cards.view", "action": "view", "label": "Bank kartalarini ko'rish"},
                    {"code": "bank_cards.create", "action": "create", "label": "Bank kartasi qo'shish"},
                    {"code": "bank_cards.edit", "action": "edit", "label": "Bank kartasini tahrirlash"},
                    {"code": "bank_cards.delete", "action": "delete", "label": "Bank kartasini o'chirish"},
                ],
            },
        ],
    },
    {
        "module": "stockentry",
        "label": "Xarid (Kirim)",
        "features": [
            {
                "feature": "entries",
                "label": "Kirim hujjatlari",
                "actions": [
                    {"code": "stockentry.view", "action": "view", "label": "Kirimlar ro'yxatini ko'rish"},
                    {"code": "stockentry.create", "action": "create", "label": "Yangi kirim qilish"},
                    {"code": "stockentry.edit", "action": "edit", "label": "Kirimni tahrirlash"},
                    {"code": "stockentry.delete", "action": "delete", "label": "Kirimni o'chirish"},
                    {"code": "stockentry.export", "action": "export", "label": "Eksport (Excel)"},
                ],
            },
            {
                "feature": "sessions",
                "label": "Kirim sessiyasi (Wizard)",
                "actions": [
                    {"code": "stockentry.session.create", "action": "create", "label": "Qoralamani qabul qilish"},
                    {"code": "stockentry.session.confirm", "action": "confirm", "label": "Kirimni tasdiqlash"},
                ],
            },
            {
                "feature": "import",
                "label": "Excel import",
                "actions": [
                    {"code": "stockentry.import.view", "action": "view", "label": "Kirim shablonini ko'rish / tahlil"},
                    {"code": "stockentry.import.create", "action": "create", "label": "Exceldan kirim qilish"},
                ],
            },
            {
                "feature": "returns",
                "label": "Ta'minotchiga qaytim",
                "actions": [
                    {"code": "stockentry.return.view", "action": "view", "label": "Qaytimlar ro'yxatini ko'rish"},
                    {"code": "stockentry.return.create", "action": "create", "label": "Ta'minotchiga tovar qaytarish"},
                    {"code": "stockentry.return.export", "action": "export", "label": "Qaytimlarni eksport qilish"},
                ],
            },
            {
                "feature": "payments",
                "label": "Ta'minotchi to'lovlari",
                "actions": [
                    {"code": "stockentry.pay.view", "action": "view", "label": "To'lovlar tarixini ko'rish"},
                    {"code": "stockentry.pay.create", "action": "create", "label": "Ta'minotchiga to'lov qilish"},
                ],
            },
        ],
    },
    {
        "module": "transfers",
        "label": "O'tkazmalar / Transferlar",
        "features": [
            {
                "feature": "transfers",
                "label": "O'tkazmalar boshqaruvi",
                "actions": [
                    {"code": "transfers.view", "action": "view", "label": "O'tkazmalar ro'yxatini ko'rish"},
                    {"code": "transfers.create", "action": "create", "label": "O'tkazma yaratish"},
                    {"code": "transfers.edit", "action": "edit", "label": "O'tkazmani tahrirlash"},
                    {"code": "transfers.delete", "action": "delete", "label": "O'tkazmani o'chirish"},
                    {"code": "transfers.approve", "action": "approve", "label": "Tasdiqlash / Qabul qilish"},
                    {"code": "transfers.reject", "action": "reject", "label": "O'tkazmani rad etish"},
                    {"code": "transfers.export", "action": "export", "label": "Eksport (Excel)"},
                ],
            }
        ],
    },
    {
        "module": "inventory",
        "label": "Inventarizatsiya",
        "features": [
            {
                "feature": "sessions",
                "label": "Inventarizatsiya sessiyalari",
                "actions": [
                    {"code": "inventory.view", "action": "view", "label": "Inventarizatsiyalar ro'yxatini ko'rish"},
                    {"code": "inventory.create", "action": "create", "label": "Yangi inventarizatsiya boshlash"},
                    {"code": "inventory.edit", "action": "edit", "label": "Sanoq kiritish / Skanerlash"},
                    {"code": "inventory.finalize", "action": "finalize", "label": "Inventarizatsiyani yakunlash"},
                    {"code": "inventory.cancel", "action": "cancel", "label": "Inventarizatsiyani bekor qilish"},
                    {"code": "inventory.export", "action": "export", "label": "Eksport (Excel)"},
                ],
            },
            {
                "feature": "low_stock",
                "label": "Kam qolgan mahsulotlar",
                "actions": [
                    {"code": "low_stock.view", "action": "view", "label": "Kam qolgan mahsulotlarni ko'rish"},
                    {"code": "low_stock.export", "action": "export", "label": "Eksport (Excel)"},
                ],
            },
        ],
    },
    {
        "module": "stock_adjustments",
        "label": "Import va Hisobdan chiqarish",
        "features": [
            {
                "feature": "import",
                "label": "Qo'lda kirim (Import)",
                "actions": [
                    {"code": "import.view", "action": "view", "label": "Qo'lda kirimlar ro'yxatini ko'rish"},
                    {"code": "import.create", "action": "create", "label": "Qo'lda kirim / Import yaratish"},
                    {"code": "import.cancel", "action": "cancel", "label": "Kirimni bekor qilish (Rollback)"},
                ],
            },
            {
                "feature": "writeoff",
                "label": "Hisobdan chiqarish (Spisaniye)",
                "actions": [
                    {"code": "writeoff.view", "action": "view", "label": "Hisobdan chiqarishlar ro'yxatini ko'rish"},
                    {"code": "writeoff.create", "action": "create", "label": "Hisobdan chiqarish yaratish"},
                    {"code": "writeoff.cancel", "action": "cancel", "label": "Hisobdan chiqarishni bekor qilish"},
                ],
            },
        ],
    },
    {
        "module": "customers_debts",
        "label": "Mijozlar va Qarzlar",
        "features": [
            {
                "feature": "customers",
                "label": "Mijozlar",
                "actions": [
                    {"code": "customers.view", "action": "view", "label": "Mijozlar ro'yxatini ko'rish"},
                    {"code": "customers.create", "action": "create", "label": "Mijoz qo'shish"},
                    {"code": "customers.edit", "action": "edit", "label": "Mijozni tahrirlash"},
                    {"code": "customers.delete", "action": "delete", "label": "Mijozni o'chirish"},
                    {"code": "customers.export", "action": "export", "label": "Eksport (Excel)"},
                ],
            },
            {
                "feature": "debts",
                "label": "Qarzlar va To'lovlar",
                "actions": [
                    {"code": "debts.view", "action": "view", "label": "Qarzlar ro'yxatini ko'rish"},
                    {"code": "debts.pay", "action": "pay", "label": "Qarz to'lovini qabul qilish"},
                ],
            },
        ],
    },
    {
        "module": "suppliers",
        "label": "Yetkazib beruvchilar",
        "features": [
            {
                "feature": "suppliers",
                "label": "Ta'minotchilar",
                "actions": [
                    {"code": "suppliers.view", "action": "view", "label": "Ta'minotchilar ro'yxatini ko'rish"},
                    {"code": "suppliers.create", "action": "create", "label": "Ta'minotchi qo'shish"},
                    {"code": "suppliers.edit", "action": "edit", "label": "Ta'minotchini tahrirlash"},
                    {"code": "suppliers.delete", "action": "delete", "label": "Ta'minotchini o'chirish"},
                    {"code": "suppliers.export", "action": "export", "label": "Eksport (Excel)"},
                ],
            }
        ],
    },
    {
        "module": "reports",
        "label": "Hisobotlar",
        "features": [
            {
                "feature": "access",
                "label": "Modulga kirish",
                "actions": [
                    {"code": "reports.view", "action": "view", "label": "Hisobotlar modulini ko'rish"},
                ],
            },
            {
                "feature": "sales_report",
                "label": "Sotuvlar hisoboti",
                "actions": [
                    {"code": "reports.sales.view", "action": "view", "label": "Ko'rish"},
                    {"code": "reports.sales.export", "action": "export", "label": "Eksport (Excel/CSV)"},
                ],
            },
            {
                "feature": "top_products_report",
                "label": "Ko'p sotilgan mahsulotlar",
                "actions": [
                    {"code": "reports.top_products.view", "action": "view", "label": "Ko'rish"},
                    {"code": "reports.top_products.export", "action": "export", "label": "Eksport (Excel/CSV)"},
                ],
            },
            {
                "feature": "products_report",
                "label": "Mahsulotlar / inventar hisoboti",
                "actions": [
                    {"code": "reports.products.view", "action": "view", "label": "Ko'rish"},
                    {"code": "reports.products.export", "action": "export", "label": "Eksport (Excel/CSV)"},
                ],
            },
            {
                "feature": "low_stock_report",
                "label": "Kam qolgan mahsulotlar",
                "actions": [
                    {"code": "reports.low_stock.view", "action": "view", "label": "Ko'rish"},
                    {"code": "reports.low_stock.export", "action": "export", "label": "Eksport (Excel/CSV)"},
                ],
            },
            {
                "feature": "product_history_report",
                "label": "Mahsulot harakatlari tarixi",
                "actions": [
                    {"code": "reports.product_history.view", "action": "view", "label": "Ko'rish"},
                    {"code": "reports.product_history.export", "action": "export", "label": "Eksport (Excel/CSV)"},
                ],
            },
            {
                "feature": "customers_report",
                "label": "Mijozlar hisoboti",
                "actions": [
                    {"code": "reports.customers.view", "action": "view", "label": "Ko'rish"},
                    {"code": "reports.customers.export", "action": "export", "label": "Eksport (Excel/CSV)"},
                ],
            },
            {
                "feature": "suppliers_report",
                "label": "Ta'minotchilar hisoboti",
                "actions": [
                    {"code": "reports.suppliers.view", "action": "view", "label": "Ko'rish"},
                    {"code": "reports.suppliers.export", "action": "export", "label": "Eksport (Excel/CSV)"},
                ],
            },
            {
                "feature": "supplier_sales_report",
                "label": "Yetkazib beruvchilar bo'yicha sotuvlar",
                "actions": [
                    {"code": "reports.supplier_sales.view", "action": "view", "label": "Ko'rish"},
                    {"code": "reports.supplier_sales.export", "action": "export", "label": "Eksport (Excel/CSV)"},
                ],
            },
            {
                "feature": "stock_leftovers_report",
                "label": "Qoldiqlar bo'yicha hisobot",
                "actions": [
                    {"code": "reports.stock_leftovers.view", "action": "view", "label": "Ko'rish"},
                    {"code": "reports.stock_leftovers.export", "action": "export", "label": "Eksport (Excel/CSV)"},
                ],
            },
            {
                "feature": "payments_report",
                "label": "To'lovlar hisoboti",
                "actions": [
                    {"code": "reports.payments.view", "action": "view", "label": "Ko'rish"},
                    {"code": "reports.payments.export", "action": "export", "label": "Eksport (Excel/CSV)"},
                ],
            },
            {
                "feature": "expenses_report",
                "label": "Chiqimlar hisoboti",
                "actions": [
                    {"code": "reports.expenses.view", "action": "view", "label": "Ko'rish"},
                    {"code": "reports.expenses.export", "action": "export", "label": "Eksport (Excel/CSV)"},
                ],
            },
        ],
    },
    {
        "module": "settings",
        "label": "Tizim sozlamalari",
        "features": [
            {
                "feature": "stores",
                "label": "Do'konlar",
                "actions": [
                    {"code": "stores.view", "action": "view", "label": "Do'konlarni ko'rish"},
                    {"code": "stores.create", "action": "create", "label": "Do'kon qo'shish"},
                    {"code": "stores.edit", "action": "edit", "label": "Do'konni tahrirlash"},
                    {"code": "stores.delete", "action": "delete", "label": "Do'konni o'chirish"},
                ],
            },
            {
                "feature": "users",
                "label": "Foydalanuvchilar",
                "actions": [
                    {"code": "users.view", "action": "view", "label": "Foydalanuvchilarni ko'rish"},
                    {"code": "users.create", "action": "create", "label": "Foydalanuvchi qo'shish"},
                    {"code": "users.edit", "action": "edit", "label": "Foydalanuvchini tahrirlash"},
                    {"code": "users.delete", "action": "delete", "label": "Foydalanuvchini o'chirish"},
                ],
            },
            {
                "feature": "roles",
                "label": "Rollar va Huquqlar",
                "actions": [
                    {"code": "roles.view", "action": "view", "label": "Rollarni ko'rish"},
                    {"code": "roles.create", "action": "create", "label": "Rol qo'shish"},
                    {"code": "roles.edit", "action": "edit", "label": "Rolni tahrirlash"},
                    {"code": "roles.delete", "action": "delete", "label": "Rolni o'chirish"},
                ],
            },
            {
                "feature": "audit",
                "label": "Amallar jurnali (Audit log)",
                "actions": [
                    {"code": "audit.view", "action": "view", "label": "Audit jurnalini ko'rish"},
                ],
            },
        ],
    },
]

# Barcha mavjud permission kodlari to'plami
ALL_PERMISSION_CODES = frozenset(
    action["code"]
    for mod in PERMISSION_HIERARCHY
    for feat in mod["features"]
    for action in feat["actions"]
)

# Eski (legacy) permission kodlari -> yangi granular kodlar xaritasi
LEGACY_PERMISSION_MAP = {
    "sales.return": ["sales.return.view", "sales.return.create"],
    "sales.archive": ["sales.archive.view", "sales.archive.restore"],
    "stockentry.import": ["stockentry.import.view", "stockentry.import.create"],
    "stockentry.return": ["stockentry.return.view", "stockentry.return.create"],
    "stockentry.pay": ["stockentry.pay.view", "stockentry.pay.create"],
    "inventory.adjust": ["products.stock.adjust", "import.create", "writeoff.create", "import.view", "writeoff.view"],
    "debts.create": ["debts.pay"],
    "products.import": ["products.import.view", "products.import.create"],
    "reports.view": [
        "reports.view",
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


def normalize_permission_codes(codes: list[str]) -> list[str]:
    """Eski/legacy permission kodlarini granular zamonaviy kodlarga o'giradi va faqat katalogda borlarini qoldiradi."""
    result: set[str] = set()
    for code in codes:
        if code in ALL_PERMISSION_CODES:
            result.add(code)
        elif code in LEGACY_PERMISSION_MAP:
            for new_code in LEGACY_PERMISSION_MAP[code]:
                if new_code in ALL_PERMISSION_CODES:
                    result.add(new_code)
    # Agar rolda biron-bir reports.* bo'lsa, "reports.view" (modulga kirish) ham avtomatik kiritiladi
    if any(c.startswith("reports.") for c in result):
        result.add("reports.view")
    return sorted(list(result))


# Har bir permission kodi uchun to'liq tushunarli label
PERMISSION_LABELS = {
    action["code"]: f"{mod['label']} → {feat['label']} → {action['label']}"
    for mod in PERMISSION_HIERARCHY
    for feat in mod["features"]
    for action in feat["actions"]
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. CATALOG FOR API & UI
# ─────────────────────────────────────────────────────────────────────────────

def catalog_for_api() -> list[dict[str, Any]]:
    """Frontend'dagi Tree / Accordion va permission matritsasi uchun katalog."""
    result = []
    for mod in PERMISSION_HIERARCHY:
        all_module_actions = [
            action
            for feat in mod["features"]
            for action in feat["actions"]
        ]
        result.append(
            {
                "module": mod["module"],
                "label": mod["label"],
                "features": mod["features"],
                "actions": all_module_actions,
            }
        )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 3. PERMISSION CHECKERS
# ─────────────────────────────────────────────────────────────────────────────

def user_permissions(user) -> frozenset[str] | None:
    """
    Userning amaldagi permission'lari:
      - None: cheklanmagan (faqat superuser);
      - frozenset(): ruxsat yo'q (autentifikatsiyasiz yoki rolsiz user);
      - frozenset([...]): roldagi kodlar.
    """
    if not getattr(user, "is_authenticated", False):
        return frozenset()
    if user.is_superuser:
        return None
    if getattr(user, "role_id", None) is None:
        return frozenset()
    return frozenset(user.role.permissions or [])


def user_has_perm(user, code: str) -> bool:
    """Foydalanuvchida berilgan permission kodi bormi?"""
    perms = user_permissions(user)
    if perms is None:
        return True  # superuser
    # Hisobotlar ierarxiyasi: aniq hisobotni ko'rish yoki eksport qilish uchun
    # avval "reports.view" (modulga kirish) bo'lishi shart
    if code.startswith("reports.") and code != "reports.view":
        if "reports.view" not in perms:
            return False
    return code in perms


def user_has_any_perm(user, codes: list[str] | tuple[str, ...]) -> bool:
    """Foydalanuvchida ko'rsatilgan kodlardan kamida bittasi bormi?"""
    perms = user_permissions(user)
    if perms is None:
        return True
    return bool(perms.intersection(codes))


# ─────────────────────────────────────────────────────────────────────────────
# 4. DRF PERMISSION CLASSES
# ─────────────────────────────────────────────────────────────────────────────

class RequirePermission(BasePermission):
    """DRF View darajasida aniq permissionni tekshirish klassi."""
    code: str | None = None

    def __init__(self, code: str | None = None):
        if code:
            self.code = code

    def has_permission(self, request, view):
        code = getattr(view, "required_permission", self.code)
        if not code:
            return True
        return user_has_perm(request.user, code)


class RequireAnyPermission(BasePermission):
    """DRF View darajasida bir nechta permissiondan kamida birini talab qilish."""
    codes: tuple[str, ...] = ()

    def __init__(self, *codes: str):
        if codes:
            self.codes = codes

    def has_permission(self, request, view):
        codes = getattr(view, "required_permissions", self.codes)
        if not codes:
            return True
        return user_has_any_perm(request.user, codes)
