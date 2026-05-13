from rest_framework.generics import get_object_or_404

from apps.store.models import StoreUser, Store


class StoreSelector:

    @staticmethod
    def get_store(pk):
        return get_object_or_404(Store, pk=pk)

    @staticmethod
    def user_has_store(user):
        return StoreUser.objects.filter(user=user, is_active=True).exists()

    @staticmethod
    def store_list():
        # ⚠️ MUAMMO [PERFORMANCE]: Store list `.all()` bilan qaytadi va serializerdagi sellerlar prefetch qilinmagan.
        # Sabab: `StoreListSerializer.get_sellers` har store uchun `user_links` query yuboradi.
        # Natija: storelar soniga proporsional N+1 query paydo bo'ladi.
        # ✅ YECHIM:
        # return Store.objects.prefetch_related(
        #     Prefetch("user_links", queryset=StoreUser.objects.filter(is_active=True).select_related("user"), to_attr="active_user_links")
        # ).order_by("name")
        # N+1: `StoreListSerializer.get_sellers` har do'kon uchun `user_links` so'raydi —
        # `prefetch_related("user_links__user")` qo'shilsa ro'yxat tezlashadi.
        return Store.objects.all()


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [store_list querysetiga user_links prefetch qo'shish]
# ═══════════════════════════════
