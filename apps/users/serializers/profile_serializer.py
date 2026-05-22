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

    class Meta:
        model = User
        fields = (
            "id",
            "is_superuser",
            "full_name",
            "phone_number",
            "email",
            "role",
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

    def get_history(self, obj) -> list:
        histories = sorted(
            obj.history.all(),
            key=lambda h: h.created_at,
            reverse=True,
        )[:5]
        return UserHistorySerializer(histories, many=True).data
