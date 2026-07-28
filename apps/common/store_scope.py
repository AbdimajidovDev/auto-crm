"""
Do'kon (store) darajasidagi ruxsat — butun loyiha uchun yagona manba.

Qoida:
  - superuser — barcha do'konlar;
  - xodim — faqat StoreUser orqali biriktirilgan (is_active=True) do'konlar.

Ilgari bu mantiq faqat `apps/contract/permissions.py` da bor edi, shuning uchun
sales/debts/inventory/transfer endpointlari scoping'siz qolgan va bir do'kon
xodimi boshqa do'konning ma'lumotini o'qiy/o'zgartira olardi. Yangi kod shu
moduldan foydalanishi kerak.
"""

from rest_framework.exceptions import PermissionDenied

from apps.store.models import StoreUser

DEFAULT_DENIED_MESSAGE = "Siz faqat o'zingizga biriktirilgan do'kon bilan ishlay olasiz"


def allowed_store_ids(user):
    """
    Foydalanuvchi ishlay oladigan do'kon ID'lari.
    None — cheklanmagan (superuser); aks holda faol biriktirilgan do'konlar to'plami.
    """
    if not (user and user.is_authenticated):
        return set()
    if user.is_superuser:
        return None
    return set(
        StoreUser.objects.filter(user=user, is_active=True).values_list("store_id", flat=True)
    )


def ensure_store_access(user, store_id, message: str = DEFAULT_DENIED_MESSAGE):
    """Do'kon foydalanuvchiga biriktirilmagan bo'lsa PermissionDenied (403)."""
    if store_id is None:
        raise PermissionDenied(message)
    allowed = allowed_store_ids(user)
    if allowed is not None and int(store_id) not in allowed:
        raise PermissionDenied(message)


def scope_queryset(qs, user, store_field: str = "store"):
    """
    Queryset'ni foydalanuvchining do'konlari bilan cheklaydi.

    `store_field` — modeldan Store'gacha bo'lgan yo'l ("store", "sale__store", ...).
    `__in` ishlatiladi (join emas), shuning uchun `distinct()` talab qilinmaydi.
    """
    allowed = allowed_store_ids(user)
    if allowed is None:
        return qs
    return qs.filter(**{f"{store_field}_id__in": allowed})
