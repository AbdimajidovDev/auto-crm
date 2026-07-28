"""
Kirim (stock entry / purchase session) uchun ruxsat va do'kon scoping qoidalari.

Qoida:
  - superuser — istalgan do'konga kirim qila oladi;
  - do'kon xodimi — faqat o'ziga biriktirilgan (StoreUser, is_active) do'kon(lar)ga;
  - RBAC roli bor user uchun qo'shimcha "stockentry.create" permission talab qilinadi
    (rolsiz eski userlar cheklanmagan — user_has_perm qoidasi bo'yicha).
"""

from rest_framework.permissions import BasePermission

from apps.common.store_scope import allowed_store_ids
from apps.common.store_scope import ensure_store_access as _ensure_store_access
from apps.users.permissions import user_has_perm

__all__ = ["CanCreateStockEntry", "allowed_store_ids", "ensure_store_access"]


class CanCreateStockEntry(BasePermission):
    """Kirim yaratish: superuser yoki stockentry.create ruxsatiga ega xodim."""

    message = "Kirim yaratish uchun ruxsat yo'q"

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return user_has_perm(user, "stockentry.create")


def ensure_store_access(user, store_id):
    """Do'kon foydalanuvchiga biriktirilmagan bo'lsa 403 qaytaradi (kirim matni bilan)."""
    _ensure_store_access(user, store_id, "Siz faqat o'z do'koningizga xarid (kirim) qila olasiz")
