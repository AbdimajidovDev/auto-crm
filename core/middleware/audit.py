# core/middleware/audit.py
"""
Amallar jurnali (audit log) middleware.

Barcha YOZUV so'rovlarini (POST/PUT/PATCH/DELETE) avtomatik jurnalga tushiradi:
kim (user), nima (modul + amal), qaysi obyekt (URL'dagi id), qachon, qayerdan
(IP) va qaysi qurilmadan (User-Agent). So'rov tanasi parollarsiz va hajmi
cheklangan holda saqlanadi.

GET so'rovlar yozilmaydi (juda ko'p shovqin bo'ladi). Login/logout jurnalga
auth view'larining o'zida tushadi (u paytda cookie hali yo'q/o'chirilgan).

Jurnal yozuvi hech qachon asosiy so'rovni buzmasligi kerak — barcha xatolar
yutiladi.
"""
import json
import logging
import re

from django.core.cache import cache

from apps.users.authentication import CookieJWTAuthentication

from .rbac import PATH_MODULE_MAP

logger = logging.getLogger("core.audit")

# Eski yozuvlarni tozalash kuniga ko'pi bilan bir marta ishlaydi
PRUNE_CACHE_KEY = "audit_log:last_prune"
PRUNE_INTERVAL_SECONDS = 24 * 60 * 60

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

ACTION_MAP = {
    "POST": "create",
    "PUT": "edit",
    "PATCH": "edit",
    "DELETE": "delete",
}

# Auth oqimi view'larda alohida yoziladi
SKIP_PREFIXES = (
    "/api/users/login/",
    "/api/users/logout/",
    "/api/users/auth/",
    "/api/users/change-password/",
)

MAX_BODY_BYTES = 4096
SENSITIVE_KEY_PARTS = ("password", "token", "secret")

_ID_RE = re.compile(r"/(\d+)/")


def client_ip(request) -> str:
    """Proxy ortida bo'lsa ham haqiqiy client IP (X-Forwarded-For birinchi)."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()[:64]
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def _sanitize(value):
    if isinstance(value, dict):
        return {
            k: ("***" if any(p in k.lower() for p in SENSITIVE_KEY_PARTS) else _sanitize(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(v) for v in value[:50]]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "…"
    return value


def _resolve_module(path: str):
    for prefix, module in PATH_MODULE_MAP:
        if path.startswith(prefix):
            return module
    return None


class AuditLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.jwt_auth = CookieJWTAuthentication()

    def __call__(self, request):
        should_log, module = self._should_log(request)
        body = self._capture_body(request) if should_log else None

        response = self.get_response(request)

        if should_log:
            try:
                self._write_log(request, response, module, body)
            except Exception:  # jurnal hech qachon so'rovni buzmasin
                logger.exception("Audit log yozib bo'lmadi: %s %s", request.method, request.path)
            self._prune_expired()

        return response

    def _prune_expired(self):
        """60 kundan eski yozuvlarni o'chiradi (kuniga ko'pi bilan bir marta)."""
        try:
            if not cache.add(PRUNE_CACHE_KEY, "1", PRUNE_INTERVAL_SECONDS):
                return  # bugun allaqachon tozalangan
            from apps.users.models import AuditLog

            deleted = AuditLog.prune_expired()
            if deleted:
                logger.info("Audit log: muddati o'tgan %d ta yozuv o'chirildi", deleted)
        except Exception:
            logger.exception("Audit logni tozalab bo'lmadi")

    def _should_log(self, request):
        if request.method not in MUTATING_METHODS:
            return False, None
        path = request.path
        if any(path.startswith(p) for p in SKIP_PREFIXES):
            return False, None
        module = _resolve_module(path)
        if module is None:
            return False, None
        return True, module

    def _capture_body(self, request):
        """JSON so'rov tanasini view o'qishidan OLDIN xavfsiz nusxalab olamiz."""
        try:
            content_type = request.content_type or ""
            if "json" not in content_type:
                return None  # multipart/form — fayllar jurnalga yozilmaydi
            raw = request.body  # Django buferlaydi, view bemalol qayta o'qiydi
            if not raw or len(raw) > MAX_BODY_BYTES:
                return None
            return _sanitize(json.loads(raw.decode("utf-8")))
        except Exception:
            return None

    def _get_user(self, request):
        # RBAC middleware allaqachon aniqlagan bo'lsa — qayta decode qilmaymiz
        user = getattr(request, "_rbac_user", None)
        if user is not None:
            return user
        try:
            result = self.jwt_auth.authenticate(request)
        except Exception:
            return None
        return result[0] if result else None

    def _write_log(self, request, response, module, body):
        from apps.users.models import AuditLog
        from apps.products.models import Product

        user = self._get_user(request)
        if user is None:
            return  # anonim urinishlar (401) jurnalga yozilmaydi

        action = ACTION_MAP.get(request.method, "edit")
        # Mahsulot statusi 'i' qilinsa — bu arxivlash
        if (
            module == "products"
            and action == "edit"
            and isinstance(body, dict)
            and str(body.get("status", "")) == Product.ProductStatus.INACTIVE
        ):
            action = "archive"

        match = _ID_RE.findall(request.path)
        object_id = match[-1] if match else ""

        AuditLog.objects.create(
            user=user,
            user_display=f"{user.full_name or ''} ({user.phone_number})".strip(),
            module=module,
            action=action,
            method=request.method,
            path=request.path[:255],
            object_id=object_id,
            status_code=getattr(response, "status_code", None),
            details=body,
            ip_address=client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:255],
        )
