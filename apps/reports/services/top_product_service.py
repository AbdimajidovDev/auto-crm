from django.db.models import Sum

from apps.reports.services.store_scope_service import StoreScopeService
from apps.sales.models import SaleItem



class TopProductsService:

    @staticmethod
    def get_top_products(*, user, date_from, date_to, limit=5, store_id=None):

        # ⚠️ MUAMMO [PERF]: filter SaleItem(65k) -> Sale JOIN orqali sale__created_at__range
        # bo'yicha boradi. Sale.created_at indekssiz — katta oraliqda range scan sekin.
        qs = SaleItem.objects.filter(
            sale__created_at__range=(date_from, date_to)
        )

        # 🔥 YANGI QISM
        qs = StoreFilterService.apply_store_filter(
            qs,
            user,
            store_id
        )

        # ⚠️ MUAMMO [PERF]: select_related("product","sale") + only(...) bu yerda BEHUDA —
        # pastda .values("product_id", "product__name") + aggregate ishlatiladi. .values() bu
        # select_related/only'ni bekor qiladi va o'zi kerakli ustunlarni tanlaydi. Bu qatorlar
        # chalkashlik keltiradi, real optimizatsiya bermaydi (o'chirib tashlash mumkin).
        qs = qs.select_related("product", "sale").only(
            "product__id",
            "product__name",
            "quantity",
            "sale__store_id",
            "sale__created_at"
        )


        # ⚠️ MUAMMO [PERF]: limit view'dan cheklanmagan holda keladi (yuqori chegara yo'q).
        # Katta limit'da SaleItem bo'yicha guruhlangan natija cheklanmay qaytishi mumkin.
        # ✅ YECHIM: limit'ni view yoki shu yerda min(limit, 50) bilan cheklash.
        data = (
            qs.values("product_id", "product__name")
            .annotate(total_sold=Sum("quantity"))
            .order_by("-total_sold", "product_id")
        )[:limit]

        return [
            {
                "product_id": i["product_id"],
                "name": i["product__name"],
                "total_sold": i["total_sold"],
            }
            for i in data
        ]



class StoreFilterService:

    @staticmethod
    def apply_store_filter(qs, user, store_id=None):

        # 🔥 superuser
        if user.is_superuser:
            if store_id:
                return qs.filter(sale__store_id=store_id)
            return qs  # hammasi

        # 🔥 oddiy user
        user_store_ids = StoreScopeService.get_user_stores(user)

        qs = qs.filter(sale__store_id__in=user_store_ids)

        # 🔥 agar store_id berilgan bo‘lsa
        if store_id:
            # ❗ VALIDATION
            # ⚠️ MUAMMO [PERF]: `int(store_id) not in user_store_ids` — user_store_ids bu
            # baholanmagan QuerySet (values_list). `in` operatori uni Python xotirasiga to'liq
            # yuklab, chiziqli qidiradi (har chaqiruvda alohida SQL + list materializatsiya).
            # ✅ YECHIM: yuqorida bir marta ro'yxatga aylantirib olish
            #   user_store_ids = list(StoreScopeService.get_user_stores(user))
            # va tekshiruvni set(user_store_ids) bilan qilish.
            if int(store_id) not in user_store_ids:
                raise PermissionError("Sizda bu storega access yo‘q")

            qs = qs.filter(sale__store_id=store_id)

        return qs


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 3
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [limit cheklash + Sale.created_at index; behuda select_related/only ni olib tashlash]
# ═══════════════════════════════
