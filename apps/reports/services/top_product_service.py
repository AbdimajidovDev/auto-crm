# services/top_products_service.py

from django.db.models import Sum

from apps.reports.services import StoreScopeService
from apps.sales.models import SaleItem



class TopProductsService:

    @staticmethod
    def get_top_products(*, user, date_from, date_to, limit=5, store_id=None):

        qs = SaleItem.objects.filter(
            sale__created_at__range=(date_from, date_to)
        )

        # 🔥 YANGI QISM
        qs = StoreFilterService.apply_store_filter(
            qs,
            user,
            store_id
        )

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
            if int(store_id) not in user_store_ids:
                raise PermissionError("Sizda bu storega access yo‘q")

            qs = qs.filter(sale__store_id=store_id)

        return qs



# class TopProductsService:
#
#     @staticmethod
#     def get_top_products(*, user, date_from, date_to, limit=5):
#
#         store_ids = StoreScopeService.get_user_stores(user)
#
#         qs = SaleItem.objects.filter(
#             sale__created_at__range=(date_from, date_to)
#         )
#
#         # 🔥 STORE FILTER
#         if store_ids is not None:
#             print(' 🔥 STORE FILTER  🔥 STORE FILTER  🔥 STORE FILTER  🔥 STORE FILTER 🔥 STORE FILTER')
#             qs = qs.filter(sale__store_id__in=store_ids)
#
#         # 🔥 CORE AGGREGATION
#         data = (
#             qs.values(
#                 "product_id",
#                 "product__name"
#             )
#             .annotate(
#                 total_sold=Sum("quantity")
#             )
#             .order_by("-total_sold")
#         )[:limit]
#
#         return [
#             {
#                 "product_id": item["product_id"],
#                 "name": item["product__name"],
#                 "total_sold": item["total_sold"],
#             }
#             for item in data
#         ]



