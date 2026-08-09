# core/middleware/rbac.py
"""
RBAC — rol asosidagi ruxsat nazorati (server darajasida).

Qoidalar:
  - faqat superuser cheklanmaydi; rolsiz user hech qanday amal bajara olmaydi
    (apps/users/permissions.py::user_permissions);
  - yozuv amallari (POST/PUT/PATCH/DELETE) barcha modullarda qat'iy tekshiriladi;
  - GET (ko'rish) faqat STRICT_VIEW_MODULES uchun tekshiriladi — qolgan
    modullarda sahifa ichi ma'lumotlari bir-biriga bog'liq (masalan POS sahifasi
    do'kon/mijoz ro'yxatini o'qiydi), shuning uchun ko'rish cheklovi frontend
    menyu/route darajasida amalga oshiriladi;
  - JWT cookie'da bo'lgani uchun (DRF darajasida autentifikatsiya) bu middleware
    tokenni o'zi ochadi; token yaroqsiz bo'lsa aralashmaydi — DRF 401 qaytaradi.

Permission kodlari: apps/users/permissions.py::PERMISSION_CATALOG.
"""
from django.http import JsonResponse

from apps.users.authentication import CookieJWTAuthentication
from apps.users.permissions import ACTION_LABELS, PERMISSION_CATALOG, user_permissions

# "sales" -> "Sotuvlar" — 403 xabarida foydalanuvchiga tushunarli nom ko'rsatish uchun
MODULE_LABELS = {entry["module"]: entry["label"] for entry in PERMISSION_CATALOG}


def denial_message(code: str) -> str:
    """
    Permission kodidan foydalanuvchiga tushunarli rad xabari yasaydi:
    "sales.create" -> "Sizga bu bo'limdan foydalanishga ruxsat yo'q:
    «Sotuvlar» bo'limida «Qo'shish» amali uchun huquq berilmagan."
    """
    module, _, action = code.partition(".")
    module_label = MODULE_LABELS.get(module, module)
    action_label = ACTION_LABELS.get(action, action)
    return (
        "Sizga bu bo'limdan foydalanishga ruxsat yo'q: "
        f"«{module_label}» bo'limida «{action_label}» amali uchun huquq berilmagan."
    )

# Tartib muhim: eng uzun prefiks birinchi tekshiriladi.
PATH_MODULE_MAP = [
    ("/api/users/customers/", "customers"),
    ("/api/users/roles/", "roles"),
    ("/api/users/audit-logs/", "audit"),
    ("/api/users/", "users"),
    ("/api/products/categories/", "categories"),
    ("/api/products/", "products"),
    ("/api/store/", "stores"),
    ("/api/contract/supplier/", "suppliers"),
    ("/api/contract/", "stockentry"),
    ("/api/transfer/", "transfers"),
    ("/api/sales/", "sales"),
    ("/api/debts/", "debts"),
    ("/api/reports/", "reports"),
    ("/api/inventory/", "inventory"),
    ("/api/writeoff/", "writeoff"),
]

# Umumiy metod→amal mappingiga tushmaydigan yo'llar: prefiks → aniq permission kodi.
# Masalan bulk-status POST bo'lsa ham "create" emas, "archive" huquqini talab qiladi.
# DIQQAT: bu tekshiruv faqat YOZUV metodlariga (POST/PUT/PATCH/DELETE) qo'llanadi —
# shu prefiks ostidagi GET'lar (ro'yxat, shablon yuklab olish) avvalgidek ochiq qoladi.
SPECIAL_PATH_CODES = [
    ("/api/products/bulk-status/", "products.archive"),
    ("/api/products/bulk-delete/", "products.delete"),

    # ── Maxsus (CRUD'dan tashqari) amallar ──
    # Sotuvlar
    ("/api/sales/sale-return/", "sales.return"),          # POST qaytim yaratish (GET list ochiq)
    ("/api/sales/archive/restore/", "sales.archive"),
    ("/api/sales/bulk-delete/", "sales.delete"),          # avval metod bo'yicha "create" talab qilinardi
    # Xarid (kirim)
    ("/api/contract/entry/import/", "stockentry.import"),  # import + analyze (template GET ochiq)
    ("/api/contract/supplier-payments/create/", "stockentry.pay"),
    # Qarzlar — bu moduldagi yozuvlar faqat to'lov
    ("/api/debts/create/", "debts.pay"),
    ("/api/debts/customer/pay/", "debts.pay"),
    # Mahsulot Excel importi (lookup/ bu ro'yxatga KIRMAYDI — u sotuv chekini
    # o'qish uchun read-only tekshiruv, metod-mapping bo'yicha qoladi)
    ("/api/products/products/import/template/", "products.view"),
    ("/api/products/products/import/lookup/", "products.view"),
    ("/api/products/products/import/", "products.import"),
    # Inventarizatsiya
    ("/api/inventory/adjust/", "inventory.adjust"),        # adjustments/ GET bunga tushmaydi
    ("/api/inventory/finalize/", "inventory.finalize"),
    ("/api/inventory/cancel/", "inventory.cancel"),
]

# ID qatnashgan yo'llar uchun regex → permission kodi (faqat yozuv metodlari).
import re as _re

SPECIAL_PATH_REGEX = [
    (_re.compile(r"^/api/transfer/\d+/(approve|reject)/$"), "transfers.approve"),
    (_re.compile(r"^/api/contract/entry/\d+/return/$"), "stockentry.return"),
]

# Auth oqimi — rolga bog'liq emas
EXEMPT_PREFIXES = (
    "/api/users/login/",
    "/api/users/logout/",
    "/api/users/auth/",
    "/api/users/change-password/",
    "/api/users/profile/",
)

# GET so'rovi ham permission talab qiladigan modullar (maxfiy ma'lumotlar)
STRICT_VIEW_MODULES = {"users", "roles", "reports", "audit"}

METHOD_ACTION_MAP = {
    "POST": "create",
    "PUT": "edit",
    "PATCH": "edit",
    "DELETE": "delete",
}


def _resolve_module(path: str):
    for prefix, module in PATH_MODULE_MAP:
        if path.startswith(prefix):
            return module
    return None


class RBACMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.jwt_auth = CookieJWTAuthentication()

    def __call__(self, request):
        denial = self._check(request)
        if denial is not None:
            return denial
        return self.get_response(request)

    def _check(self, request):
        # CORS preflight va exempt yo'llar
        if request.method == "OPTIONS":
            return None
        path = request.path
        if any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return None

        module = _resolve_module(path)
        if module is None:
            return None

        # Maxsus yo'llar: yozuv metodlarida umumiy metod→amal mappingi o'rniga
        # aniq permission kodi tekshiriladi (GET'lar ochiqligicha qoladi)
        special_code = None
        if request.method in METHOD_ACTION_MAP:
            special_code = next(
                (code for prefix, code in SPECIAL_PATH_CODES if path.startswith(prefix)),
                None,
            )
            if special_code is None:
                special_code = next(
                    (code for pattern, code in SPECIAL_PATH_REGEX if pattern.match(path)),
                    None,
                )
        if special_code is not None:
            user = self._get_user(request)
            if user is None:
                return None
            request._rbac_user = user
            perms = user_permissions(user)
            if perms is None or special_code in perms:
                return None
            return JsonResponse(
                {"detail": denial_message(special_code), "permission": special_code},
                status=403,
            )

        action = METHOD_ACTION_MAP.get(request.method, "view")
        if action == "view" and module not in STRICT_VIEW_MODULES:
            return None

        user = self._get_user(request)
        if user is None:
            # Token yo'q/yaroqsiz — DRF o'zi 401 qaytaradi
            return None

        # Keyingi middleware'lar (masalan audit) qayta decode qilmasligi uchun
        request._rbac_user = user

        perms = user_permissions(user)
        if perms is None:
            return None  # superuser — cheklanmagan

        code = f"{module}.{action}"
        allowed = code in perms
        # Rollar ro'yxatini user boshqaruvi sahifasi ham o'qiydi
        if not allowed and code == "roles.view":
            allowed = bool(perms & {"users.view", "users.create", "users.edit"})

        if allowed:
            return None

        return JsonResponse(
            {"detail": denial_message(code), "permission": code},
            status=403,
        )

    def _get_user(self, request):
        try:
            result = self.jwt_auth.authenticate(request)
        except Exception:
            return None
        if result is None:
            return None
        user, _token = result
        return user
