from rest_framework import serializers

from apps.users.models import User
from apps.users.serializers import UserHistorySerializer


from rest_framework import serializers
from apps.store.models import Store

class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ("id", "name", "phone_number", "address", "type", "is_active")



class ProfileSerializer(serializers.ModelSerializer):
    history = serializers.SerializerMethodField()
    # ⚠️ MUAMMO [PERFORMANCE]: `stores` SerializerMethodField ichida DB query qiladi.
    # Sabab: har profile serializationda Store queryset alohida ishlaydi; prefetch/contextdan foydalanilmagan.
    # Natija: profile serializer qayta ishlatilsa yoki history bilan birga og'irlashsa ortiqcha DB query paydo bo'ladi.
    # ✅ YECHIM:
    # stores = StoreSerializer(source="prefetched_stores", many=True, read_only=True)
    # Viewda user storelarini oldindan prefetch/context orqali uzatish.
    stores = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "is_superuser",
            "full_name",
            "phone_number",
            "email",
            "stores",
            "history",
        )

        extra_kwargs = {
            "id": {"read_only": True},
            "is_superuser": {'read_only': True},
            "phone_number": {'read_only': True},
            "email": {'read_only': True},
        }

    def get_history(self, obj):
        histories = obj.history.all().order_by('-created_at')[:5]
        return UserHistorySerializer(histories, many=True).data

    def get_stores(self, obj):
        # Eslatma: profil serializer har safar `obj` o'rniga `request.user` bo'yicha `Store` qidiradi —
        # serializer bir necha marta ishlatilsa ham bir xil so'rov takrorlanadi; prefetch yoki
        # view darajasida kontekst orqali uzatish ixtiyoriy optimallashtirish.
        request = self.context.get("request")
        user = request.user

        # 🔐 SUPERUSER → hamma store
        if user.is_superuser:
            stores = Store.objects.filter(is_active=True)

        else:
            # 🔐 faqat o‘ziga tegishli storelar
            stores = Store.objects.filter(
                user_links__user=user,
                user_links__is_active=True,
                is_active=True
            ).distinct()

        return StoreSerializer(stores, many=True).data


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [ProfileSerializer stores querysini view/prefetch qatlamiga chiqarish]
# ═══════════════════════════════


#
# class ProfileSerializer(serializers.ModelSerializer):
#     history = UserHistorySerializer(many=True, read_only=True)
#
#     class Meta:
#         model = User
#         fields = ("id", "is_superuser", "full_name", "phone_number", "email", "history")
#
#         extra_kwargs = {
#             "id": {"read_only": True},
#             "is_superuser": {'read_only': True},
#             "phone_number": {'read_only': True},
#             "email": {'read_only': True},
#         }
