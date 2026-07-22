"""
Hisobot so'rovlarini foydalanuvchi huquqiga moslash.

Superadmin istalgan do'kon (yoki 'all' — umumiy) bo'yicha hisobot oladi.
Do'kon admini esa faqat O'Z do'kon(lar)i bo'yicha — boshqa do'kon yoki
'all' so'ralsa ham server o'z do'koniga majburlaydi (frontendni chetlab
o'tib bo'lmaydi).
"""
from rest_framework.exceptions import ValidationError

from apps.contract.permissions import allowed_store_ids


def scope_report_params(request):
    """
    Query paramlarning nusxasini qaytaradi, store_id foydalanuvchi
    huquqiga moslangan holda.
    """
    params = request.GET.copy()
    allowed = allowed_store_ids(request.user)
    if allowed is None:
        # superuser — cheklanmagan
        return params
    if not allowed:
        raise ValidationError({"store_id": "Sizga do'kon biriktirilmagan"})

    requested = params.get("store_id")
    if requested and requested.isdigit() and int(requested) in allowed:
        return params

    # 'all', boshqa do'kon yoki bo'sh — o'z do'koniga majburlanadi
    params["store_id"] = str(sorted(allowed)[0])
    return params
