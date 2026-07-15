"""
Kirim (stock entry / purchase session) uchun ruxsat va do'kon scoping qoidalari.

Qoida:
  - superuser — istalgan do'konga kirim qila oladi;
  - do'kon xodimi — faqat o'ziga biriktirilgan (StoreUser, is_active) do'kon(lar)ga;
  - RBAC roli bor user uchun qo'shimcha "stockentry.create" permission talab qilinadi
    (rolsiz eski userlar cheklanmagan — user_has_perm qoidasi bo'yicha).
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.store.models import StoreUser
from apps.users.permissions import user_has_perm


class CanCreateStockEntry(BasePermission):
    """Kirim yaratish: superuser yoki stockentry.create ruxsatiga ega xodim."""

    message = "Kirim yaratish uchun ruxsat yo'q"

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return user_has_perm(user, "stockentry.create")


def allowed_store_ids(user):
    """
    Foydalanuvchi kirim qila oladigan do'kon ID'lari.
    None — cheklanmagan (superuser); aks holda faol biriktirilgan do'konlar to'plami.
    """
    if user.is_superuser:
        return None
    return set(
        StoreUser.objects.filter(user=user, is_active=True).values_list("store_id", flat=True)
    )


def ensure_store_access(user, store_id):
    """Do'kon foydalanuvchiga biriktirilmagan bo'lsa 403 qaytaradi."""
    allowed = allowed_store_ids(user)
    if allowed is not None and int(store_id) not in allowed:
        raise PermissionDenied("Siz faqat o'z do'koningizga kirim qila olasiz")
