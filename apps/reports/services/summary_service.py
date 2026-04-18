from django.db.models import Sum, Count, F, DecimalField, ExpressionWrapper
from apps.sales.models import Sale, SaleItem


class SummaryService:

    @staticmethod
    def get(date_from, date_to, store_ids):

        sales = Sale.objects.filter(created_at__range=(date_from, date_to))

        if store_ids:
            sales = sales.filter(store_id__in=store_ids)

        summary = sales.aggregate(
            total_revenue=Sum("total_amount"),
            total_orders=Count("id"),
            total_customers=Count("customer", distinct=True),
        )

        items = SaleItem.objects.filter(
            sale__created_at__range=(date_from, date_to)
        )

        if store_ids:
            items = items.filter(sale__store_id__in=store_ids)

        total_profit = items.aggregate(
            profit=Sum(
                ExpressionWrapper(
                    (F("unit_price") - F("purchase_price")) * F("quantity"),
                    output_field=DecimalField()
                )
            )
        )["profit"] or 0

        revenue = summary["total_revenue"] or 0
        orders = summary["total_orders"] or 0

        return {
            "totalRevenue": revenue,
            "totalProfit": total_profit,
            "totalExpenses": revenue - total_profit,
            "totalOrders": orders,
            "averageOrderValue": revenue / orders if orders else 0,
            "totalCustomers": summary["total_customers"] or 0,
        }
