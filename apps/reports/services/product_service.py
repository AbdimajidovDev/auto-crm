# from django.db.models import Sum
# from apps.sales.models import SaleItem
#
#
# class ProductService:
#
#     @staticmethod
#     def top_products(date_from, date_to, store_ids):
#
#         qs = SaleItem.objects.filter(
#             sale__created_at__range=(date_from, date_to)
#         )
#
#         if store_ids:
#             qs = qs.filter(sale__store_id__in=store_ids)
#
#         data = (
#             qs.values("product_id", "product__name")
#             .annotate(
#                 totalSold=Sum("quantity"),
#                 totalRevenue=Sum("total_price"),
#             )
#             .order_by("-totalSold")[:5]
#         )
#
#         return [
#             {
#                 "rank": i + 1,
#                 "productId": d["product_id"],
#                 "name": d["product__name"],
#                 "totalSold": d["totalSold"],
#                 "totalRevenue": d["totalRevenue"],
#             }
#             for i, d in enumerate(data)
#         ]
