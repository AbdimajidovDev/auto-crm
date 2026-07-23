from rest_framework import serializers

from apps.store.models import Store
from apps.users.models import User


# ─────────────────────────────────────────────
#  Store serializer — role bilan
# ─────────────────────────────────────────────
class ProfileStoreSerializer(serializers.ModelSerializer):
    """
    Store ma'lumotlari + foydalanuvchining shu do'kondagi roli.
    role — StoreUser.role dan annotate orqali keladi (N+1 yo'q).
    Superuser uchun role='superuser' — StoreUser mavjud bo'lmasa ham.
    """
    role = serializers.SerializerMethodField()

    class Meta:
        model = Store
        fields = (
            "id",
            "name",
            "phone_number",
            "address",
            "type",
            "is_active",
            "role",
        )

    def get_role(self, obj) -> str:
        # superuser bo'lsa — barcha do'konlar uchun 'superuser'
        request = self.context.get("request")
        if request and request.user.is_superuser:
            return "superuser"
        # annotate orqali kelgan role_in_store (view darajasida prefetch + annotate)
        return getattr(obj, "role_in_store", None)


# ─────────────────────────────────────────────
#  History serializer
# ─────────────────────────────────────────────
class UserHistorySerializer(serializers.ModelSerializer):
    class Meta:
        from apps.users.models import UserHistory  # loyihangizdagi model
        model = UserHistory
        fields = ("id", "action", "ip_address", "user_agent", "created_at")


# ─────────────────────────────────────────────
#  Profile serializer
# ─────────────────────────────────────────────
class ProfileSerializer(serializers.ModelSerializer):
    stores = ProfileStoreSerializer(source="prefetched_stores", many=True, read_only=True)
    history = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    # RBAC: tizim roli va amaldagi permission'lar.
    # permissions=None — cheklanmagan (superuser yoki rolsiz user).
    role_id = serializers.IntegerField(read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True, default=None)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "is_superuser",
            "full_name",
            "phone_number",
            "email",
            "role",
            "role_id",
            "role_name",
            "permissions",
            "stores",
            "history",
        )
        extra_kwargs = {
            "id": {"read_only": True},
            "is_superuser": {"read_only": True},
            "phone_number": {"read_only": True},
            "email": {"read_only": True},
        }

    def get_role(self, obj) -> str:
        if obj.is_superuser:
            return "superuser"
        # prefetched_stores da annotate qilingan role_in_store dan olamiz
        stores = getattr(obj, "prefetched_stores", None)
        if stores:
            first = next(iter(stores), None)
            if first:
                return getattr(first, "role_in_store", None)
        return None

    def get_permissions(self, obj):
        # None — cheklanmagan; ro'yxat — faqat shu kodlarga ruxsat
        from apps.users.permissions import user_permissions
        perms = user_permissions(obj)
        return None if perms is None else sorted(perms)

    def get_history(self, obj) -> list:
        # ✅ BAJARILDI: view DB darajasida LIMIT 5 bilan `recent_history` beradi —
        # butun jadvalni yuklab Pythonda sort+slice qilish yo'q qilindi.
        # To'liq (sahifalangan) ro'yxat: GET /users/history/ (LoginHistoryListAPIView).
        recent = getattr(obj, "recent_history", None)
        if recent is None:
            # Zaxira yo'l: view atributni bermagan holatda ham 5 tadan oshmaydi
            recent = obj.history.all().order_by("-created_at")[:5]
        return UserHistorySerializer(recent, many=True).data


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1  (get_history barcha history'ni Pythonda sort+slice qiladi — DB LIMIT emas)
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak:
#   [1] history'ni DB darajasida oxirgi 5 qatorga cheklab, serializerda qayta sort qilmaslik
# ═══════════════════════════════
