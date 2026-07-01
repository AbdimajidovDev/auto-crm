# ⚠️ MUAMMO [ARXITEKTURA]: Butunlay kommentlangan — O'LIK/DUBLIKAT kod. Jonli SummaryService
#   `reports/services/report_service.py` ichida. O'chirilishi tavsiya etiladi. GET perf ta'siri yo'q.
# from datetime import timedelta
#
# from django.db.models import Sum, Count, F, DecimalField, ExpressionWrapper, OuterRef, Subquery, Value, Q
# from django.db.models.functions import Coalesce
#
# from apps.sales.models import Sale, SaleItem, SaleReturn
#
#
# class SummaryService:
#
#     @staticmethod
#     def get(date_from, date_to, store_ids):
#
#         sales = Sale.objects.filter(
#             created_at__gte=date_from,
#             created_at__lt=date_to + timedelta(days=1)
#         ).exclude(status=Sale.Status.RETURNED)
#
#         if store_ids:
#             sales = sales.filter(store_id__in=store_ids)
#
#         # 🔥 RETURN SUBQUERY
#         returns_subquery = (
#             SaleReturn.objects
#             .filter(sale_id=OuterRef("id"))
#             .values("sale")
#             .annotate(total=Sum("total_refund"))
#             .values("total")[:1]
#         )
#
#         sales = sales.annotate(
#             refunded=Coalesce(
#                 Subquery(returns_subquery),
#                 Value(0, output_field=DecimalField())
#             )
#         )
#
#         summary = sales.aggregate(
#             total_revenue=Sum(F("total_amount") - F("refunded")),
#             total_orders=Count("id"),
#             total_customers=Count(
#                 "customer",
#                 distinct=True,
#                 filter=Q(customer__isnull=False)
#             ),
#         )
#
#         # 🔥 ITEMS
#         items = SaleItem.objects.filter(
#             sale__created_at__gte=date_from,
#             sale__created_at__lt=date_to + timedelta(days=1),
#             sale__status__in=[Sale.Status.PAID, Sale.Status.PARTIAL]
#         )
#
#         if store_ids:
#             items = items.filter(sale__store_id__in=store_ids)
#
#         # 🔥 PROFIT (RETURN ACCOUNTING YO‘Q — BU HALI LIMITATION)
#         total_profit = items.aggregate(
#             profit=Sum(
#                 ExpressionWrapper(
#                     (F("unit_price") - F("purchase_price")) * F("quantity"),
#                     output_field=DecimalField()
#                 )
#             )
#         )["profit"] or 0
#
#         revenue = summary["total_revenue"] or 0
#         orders = summary["total_orders"] or 0
#
#         return {
#             "totalRevenue": revenue,
#             "totalProfit": total_profit,
#             "totalExpenses": revenue - total_profit,
#             "totalOrders": orders,
#             "averageOrderValue": revenue / orders if orders else 0,
#             "totalCustomers": summary["total_customers"] or 0,
#         }
