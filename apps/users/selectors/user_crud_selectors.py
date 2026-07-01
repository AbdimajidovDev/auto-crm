# users/selectors.py

from apps.users.models import User


class UserSelector:

    @staticmethod
    def get_user_by_id(user_id: int) -> User:
        return User.objects.filter(id=user_id).only(
            "id", "phone_number", "full_name", "is_staff"
        ).first()

    @staticmethod
    def get_user_by_phone(phone: str) -> User:
        return User.objects.filter(phone_number=phone).first()

    @staticmethod
    def list_users():
        # ✅ YAXSHI: `.only(...)` bilan faqat kerakli ustunlar SELECT qilinadi — ortiqcha maydon (parol hashi va h.k.) tortilmaydi.
        # ⚠️ MUAMMO [PERF]: Bu selector paginationsiz va prefetchsiz — agar to'g'ridan-to'g'ri view'da ishlatilsa
        # (UsersListView'da class-level `queryset` sifatida ulangan, lekin `get_queryset()` uni qayta yozadi),
        # butun jadval bir javobda qaytishi va `UserSerializer` store maydonlarida N+1 yuzaga kelishi mumkin.
        # ✅ YECHIM: ro'yxat view'i pagination + `store_links` prefetch bilan ishlashi kerak (get_queryset'dagidek).
        return User.objects.all().only(
            "id", "is_staff", "full_name", "phone_number", "email",
            "is_active", "created_at", "updated_at"
        )


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1  (list_users paginationsiz/prefetchsiz — ro'yxatda to'g'ridan ishlatilsa xavfli)
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak:
#   [1] list_users natijasini har doim pagination + store prefetch bilan qo'llash
# ═══════════════════════════════