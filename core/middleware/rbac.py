"""
core/middleware/rbac.py
Granular Role-Based Access Control (RBAC) Middleware.

Asosiy qoidalar:
  - Superuser (is_superuser=True) barcha ruxsatlarga ega;
  - Rolsiz foydalanuvchi hech qanday ma'lumotga yoki amalga ega emas (fail-closed);
  - Barcha yozuv amallari (POST/PUT/PATCH/DELETE) va maxsus xavfsizlik talab qiluvchi
    GET so'rovlari (hisobotlar, eksportlar, audit, foydalanuvchilar, rollar)
    server darajasida majburiy tekshiriladi;
  - Report / Export / Import / Stock adjust / Cancel amallari uchun aniq granular kodlar tekshiriladi.
"""
import re
from django.http import JsonResponse

from apps.users.authentication import CookieJWTAuthentication
from apps.users.permissions import (
    PERMISSION_LABELS,
    user_has_any_perm,
    user_has_perm,
    user_permissions,
)


def denial_message(code: str) -> str:
    """Foydalanuvchiga tushunarli rad xabari."""
    full_label = PERMISSION_LABELS.get(code)
    if full_label:
        return (
            "Sizga ushbu bo'lim yoki amal uchun ruxsat berilmagan: "
            f"«{full_label}» huquqi talab qilinadi."
        )
    return f"Sizga bu amalni bajarish uchun ruxsat yo'q (Talab qilinadigan huquq: {code})."


# Auth oqimi — ruxsat talab qilinmaydigan yo'llar
EXEMPT_PREFIXES = (
    "/api/users/login/",
    "/api/users/logout/",
    "/api/users/auth/",
    "/api/users/change-password/",
    "/api/users/profile/",
    "/api/users/history/",
)

# Aniq yo'llar uchun permission xaritasi: (path_prefix, HTTP_method_or_None, required_permission)
# None metod — barcha metodlarga tegishli degani
EXACT_PATH_PERMISSIONS = [
    # ── Mahsulotlar & Eksport ──
    ("/api/products/export/", "GET", "products.export"),
    ("/api/products/categories/export/", "GET", "categories.export"),
    ("/api/products/bulk-status/", "POST", "products.archive"),
    ("/api/products/bulk-delete/", "POST", "products.delete"),
    ("/api/products/products/import/template/", "GET", "products.import.view"),
    ("/api/products/products/import/", "POST", "products.import.create"),

    # ── Savdo & Eksport ──
    ("/api/sales/export/", "GET", "sales.export"),
    ("/api/sales/bulk-delete/", "POST", "sales.delete"),
    ("/api/sales/archive/restore/", "POST", "sales.archive.restore"),
    ("/api/sales/archive/", "GET", "sales.archive.view"),
    ("/api/sales/sale-return/list/", "GET", "sales.return.view"),
    ("/api/sales/sale-return/", "POST", "sales.return.create"),

    # ── Xarid (Kirim) ──
    ("/api/contract/entry/export/", "GET", "stockentry.export"),
    ("/api/contract/entry/returns/export/", "GET", "stockentry.return.export"),
    ("/api/contract/entry/returns/", "GET", "stockentry.return.view"),
    ("/api/contract/entry/import/template/", "GET", "stockentry.import.view"),
    ("/api/contract/entry/import/analyze/", "POST", "stockentry.import.view"),
    ("/api/contract/entry/import/", "POST", "stockentry.import.create"),
    ("/api/contract/entry/session/", "POST", "stockentry.session.create"),
    ("/api/contract/supplier/export/", "GET", "suppliers.export"),
    ("/api/contract/supplier-payments/create/", "POST", "stockentry.pay.create"),

    # ── O'tkazmalar ──
    ("/api/transfer/export/", "GET", "transfers.export"),

    # ── Inventarizatsiya & Qoldiqlar ──
    ("/api/inventory/export/", "GET", "inventory.export"),
    ("/api/inventory/low-stock/export/", "GET", "low_stock.export"),
    ("/api/inventory/low-stock/history/", "GET", "low_stock.view"),
    ("/api/inventory/low-stock/", "GET", "low_stock.view"),
    ("/api/inventory/finalize/", "POST", "inventory.finalize"),
    ("/api/inventory/cancel/", "POST", "inventory.cancel"),

    # ── Mijozlar & Qarzlar ──
    ("/api/users/customers/export/", "GET", "customers.export"),
    ("/api/debts/create/", "POST", "debts.pay"),
    ("/api/debts/customer/pay/", "POST", "debts.pay"),

    # ── Audit & Rollar ──
    ("/api/users/audit-logs/", "GET", "audit.view"),
    ("/api/users/roles/catalog/", "GET", None),  # Autentifikatsiyadan o'tgan har qanday xodim ko'rishi mumkin
]

# Regex orqali tekshiriladigan yo'llar: (regex, method_or_None, required_permission)
REGEX_PATH_PERMISSIONS = [
    (re.compile(r"^/api/products/\d+/update-stocks/$"), "POST", "products.stock.adjust"),
    (re.compile(r"^/api/products/\d+/history/$"), "GET", "products.history.view"),
    (re.compile(r"^/api/transfer/\d+/approve/$"), "POST", "transfers.approve"),
    (re.compile(r"^/api/transfer/\d+/reject/$"), "POST", "transfers.reject"),
    (re.compile(r"^/api/contract/entry/\d+/return/$"), "POST", "stockentry.return.create"),
    (re.compile(r"^/api/contract/entry/\d+/returns/$"), "GET", "stockentry.return.view"),
    (re.compile(r"^/api/contract/entry/session/\d+/receive/$"), "POST", "stockentry.session.create"),
    (re.compile(r"^/api/contract/entry/session/\d+/confirm/$"), "POST", "stockentry.session.confirm"),
    (re.compile(r"^/api/contract/supplier/\d+/payments/$"), "GET", "stockentry.pay.view"),
]

# Modul darajasidagi standart CRUD mapping
MODULE_PREFIX_MAP = [
    ("/api/users/customers/", "customers"),
    ("/api/users/roles/", "roles"),
    ("/api/users/", "users"),
    ("/api/products/categories/", "categories"),
    ("/api/products/brand/", "brands"),
    ("/api/products/measurements/", "units"),
    ("/api/products/store-product/locations/", "locations"),
    ("/api/products/", "products"),
    ("/api/store/", "stores"),
    ("/api/contract/supplier/", "suppliers"),
    ("/api/contract/", "stockentry"),
    ("/api/transfer/", "transfers"),
    ("/api/sales/bank-cards/", "bank_cards"),
    ("/api/sales/", "sales"),
    ("/api/debts/", "debts"),
    ("/api/inventory/", "inventory"),
    ("/api/writeoff/", "writeoff"),
]

# Audit middleware bilan moslik uchun
PATH_MODULE_MAP = MODULE_PREFIX_MAP

# Maxfiy / xavfsiz ma'lumotlar — bu modullarda GET so'rovi ham permission talab qiladi
STRICT_VIEW_MODULES = {"users", "roles", "audit", "reports"}

METHOD_ACTION_MAP = {
    "POST": "create",
    "PUT": "edit",
    "PATCH": "edit",
    "DELETE": "delete",
}


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
        if request.method == "OPTIONS":
            return None
        path = request.path
        if any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return None

        # 1. Aniq yo'llar bo'yicha tekshiruv (Exact match)
        for prefix, req_method, code in EXACT_PATH_PERMISSIONS:
            if path.startswith(prefix):
                if req_method is None or request.method == req_method:
                    if code is None:
                        return None  # Maxsus ochiq yo'l (masalan /roles/catalog/)
                    return self._enforce_permission(request, code)

        # 2. Regex yo'llar bo'yicha tekshiruv
        for pattern, req_method, code in REGEX_PATH_PERMISSIONS:
            if pattern.match(path):
                if req_method is None or request.method == req_method:
                    return self._enforce_permission(request, code)

        # 3. Hisobotlar moduli maxsus tekshiruvi (/api/reports/)
        if path.startswith("/api/reports/"):
            return self._check_reports_permission(request)

        # 4. Inventarizatsiya / Adjust / Cancel maxsus tekshiruvi
        if path.startswith("/api/inventory/adjust/"):
            return self._check_inventory_adjust_permission(request)
        if path.startswith("/api/inventory/adjustments/") and "/cancel/" in path:
            return self._check_stock_adjustment_cancel_permission(request)

        # 5. Modul CRUD tekshiruvi
        for prefix, module in MODULE_PREFIX_MAP:
            if path.startswith(prefix):
                action = METHOD_ACTION_MAP.get(request.method, "view")
                if action == "view" and module not in STRICT_VIEW_MODULES:
                    return None  # Umumiy o'qish (POS mahsulot/mijoz qidirish uchun)
                code = f"{module}.{action}"
                return self._enforce_permission(request, code)

        return None

    def _get_user(self, request):
        if hasattr(request, "_rbac_user"):
            return request._rbac_user
        # DRF test client force_authenticate tekshiruvi:
        force_user = (
            getattr(request, "_force_auth_user", None)
            or getattr(getattr(request, "_request", None), "_force_auth_user", None)
            or getattr(getattr(request, "wsgi_request", None), "_force_auth_user", None)
        )
        if force_user:
            request._rbac_user = force_user
            return force_user

        # Django session yoki request.user
        if getattr(request, "user", None) and getattr(request.user, "is_authenticated", False):
            request._rbac_user = request.user
            return request.user
        try:
            result = self.jwt_auth.authenticate(request)
        except Exception:
            return None
        if result is None:
            return None
        user, _token = result
        request._rbac_user = user
        return user

    def _enforce_permission(self, request, code: str):
        user = self._get_user(request)
        if user is None:
            return None  # DRF o'zi 401 qaytaradi
        perms = user_permissions(user)
        if perms is None or code in perms:
            return None
        # Fallback ruxsatlar (masalan categories.view bo'lsa va products.view bor bo'lsa)
        if code in ("products.stock.view", "products.history.view", "categories.view", "brands.view", "units.view", "locations.view") and "products.view" in perms:
            return None
        if code == "roles.view" and bool(perms & {"users.view", "users.create", "users.edit"}):
            return None

        return JsonResponse(
            {"detail": denial_message(code), "permission": code},
            status=403,
        )

    def _check_reports_permission(self, request):
        user = self._get_user(request)
        if user is None:
            return None
        perms = user_permissions(user)
        if perms is None:
            return None

        # 1. Hisobotlar moduliga umumiy kirish huquqi (reports.view) tekshiruvi
        if "reports.view" not in perms:
            return JsonResponse(
                {"detail": "Sizda hisobotlar moduliga kirish uchun ruxsat yo'q.", "permission": "reports.view"},
                status=403,
            )

        report_type = request.GET.get("report_type")
        path = request.path

        # 2. Eksport endpointlari (reports.<type>.export)
        if path.startswith("/api/reports/builder/export/") or path.startswith("/api/reports/export/"):
            if report_type:
                code = f"reports.{report_type}.export"
                if code not in perms:
                    return JsonResponse(
                        {"detail": denial_message(code), "permission": code},
                        status=403,
                    )
            return None

        # 3. Hisobot yaratish / ko'rish endpointlari (reports.<type>.view)
        if path.startswith("/api/reports/builder/") or path.startswith("/api/reports/generate/"):
            if not path.startswith("/api/reports/builder/meta/"):
                if report_type:
                    code = f"reports.{report_type}.view"
                    if code not in perms:
                        return JsonResponse(
                            {"detail": denial_message(code), "permission": code},
                            status=403,
                        )
            return None

        return None

    def _check_inventory_adjust_permission(self, request):
        user = self._get_user(request)
        if user is None:
            return None
        perms = user_permissions(user)
        if perms is None:
            return None

        # POST /api/inventory/adjust/ (StockAdjustment yaratish)
        adj_type = request.POST.get("type") or getattr(request, "data", {}).get("type")
        if adj_type == "import":
            code = "import.create"
        elif adj_type == "write_off":
            code = "writeoff.create"
        else:
            code = "products.stock.adjust"

        if code not in perms and "inventory.adjust" not in perms:
            return JsonResponse(
                {"detail": denial_message(code), "permission": code},
                status=403,
            )
        return None

    def _check_stock_adjustment_cancel_permission(self, request):
        user = self._get_user(request)
        if user is None:
            return None
        perms = user_permissions(user)
        if perms is None:
            return None

        # POST /api/inventory/adjustments/<pk>/cancel/
        match = re.search(r"/api/inventory/adjustments/(\d+)/cancel/", request.path)
        if match:
            from apps.inventory.models import StockAdjustment
            adj = StockAdjustment.objects.filter(pk=match.group(1)).first()
            if adj:
                if adj.type == StockAdjustment.Type.IMPORT:
                    code = "import.cancel"
                elif adj.type == StockAdjustment.Type.WRITE_OFF:
                    code = "writeoff.cancel"
                else:
                    code = "products.stock.adjust"

                if code not in perms and "inventory.cancel" not in perms:
                    return JsonResponse(
                        {"detail": denial_message(code), "permission": code},
                        status=403,
                    )
        return None
