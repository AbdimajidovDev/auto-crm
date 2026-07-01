from apps.store.models import StoreUser


class StoreScopeService:

    @staticmethod
    def get_user_stores(user):
        if user.is_superuser:
            return None  # barcha store

        return StoreUser.objects.filter(
            user=user,
            is_active=True
        ).values_list("store_id", flat=True)



class ReportStoreScope:

    @staticmethod
    def resolve(store_id):
        if store_id and store_id != "all":
            return [int(store_id)]
        return None  # all stores


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Izoh: ✅ YAXSHI — `get_user_stores` `StoreUser(user, is_active)` bo'yicha indeksli filtr, `values_list` bilan
#   faqat store_id qaytaradi (yengil). Natija lazy queryset — report so'rovi ichida `store_id__in` sifatida ishlatilib
#   subquery/JOIN ga tushadi, alohida N+1 yo'q.
# Prioritet bo'yicha birinchi hal qilinishi kerak: [—]
# ═══════════════════════════════
