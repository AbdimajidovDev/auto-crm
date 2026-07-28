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
from apps.users.permissions import user_permissions

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
SPECIAL_PATH_CODES = [
    ("/api/products/bulk-status/", "products.archive"),
    ("/api/products/bulk-delete/", "products.delete"),
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

        # Maxsus yo'llar: metoddan qat'i nazar aniq permission kodi tekshiriladi
        special_code = next(
            (code for prefix, code in SPECIAL_PATH_CODES if path.startswith(prefix)),
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
                {"detail": f"Ruxsat yo'q: bu amal uchun '{special_code}' huquqi kerak."},
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
            {"detail": f"Ruxsat yo'q: bu amal uchun '{code}' huquqi kerak."},
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
