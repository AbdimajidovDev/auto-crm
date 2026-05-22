# from django.db.models import Sum, Count, OuterRef, Subquery
# from apps.sales.models import Sale, SaleReturn
# from django.db.models import Sum, Count, F, Value, DecimalField
# from django.db.models.functions import Coalesce
#
# from django.db.models import Sum, Count, F, Value, DecimalField, Q, OuterRef, Subquery
# from django.db.models.functions import Coalesce
# from datetime import timedelta
#
#
# class BranchService:
#
#     @staticmethod
#     def get(date_from, date_to, store_ids):
#
#         qs = Sale.objects.filter(
#             created_at__gte=date_from,
#             created_at__lt=date_to + timedelta(days=1)
#         ).exclude(status=Sale.Status.RETURNED)
#
#         if store_ids:
#             qs = qs.filter(store_id__in=store_ids)
#
#         returns_subquery = (
#             SaleReturn.objects
#             .filter(sale_id=OuterRef("id"))
#             .values("sale")
#             .annotate(total=Sum("total_refund"))
#             .values("total")[:1]
#         )
#
#         qs = qs.annotate(
#             refunded=Coalesce(
#                 Subquery(returns_subquery),
#                 Value(0, output_field=DecimalField())
#             )
#         )
#
#         return list(
#             qs.values("store_id", "store__name")
#             .annotate(
#                 revenue=Sum(F("total_amount") - F("refunded")),
#                 orders=Count("id"),
#                 customers=Count("customer", distinct=True, filter=Q(customer__isnull=False)),
#             )
#         )
