from django.db.models.functions import TruncWeek, TruncMonth, TruncDay
from django.db.models import Sum, F, ExpressionWrapper, DecimalField


class ChartService:

    @staticmethod
    def get_turnover_chart(sales_qs, filter_type):

        if filter_type == "weekly":
            trunc = TruncDay("created_at")
            label_format = "%Y-%m-%d"

        elif filter_type == "monthly":
            trunc = TruncWeek("created_at")
            label_format = "Week %W"

        else:  # yearly
            trunc = TruncMonth("created_at")
            label_format = "%Y-%m"

        data = (
            sales_qs
            .annotate(period=trunc)
            .values("period")
            .annotate(total=Sum("total_amount"))
            .order_by("period")
        )

        labels = [i["period"].strftime(label_format) for i in data]
        values = [i["total"] for i in data]

        return {
            "labels": labels,
            "data": values
        }


# class ChartService:

    @staticmethod
    def profit_trend(items_qs, filter_type):

        trunc = TruncWeek("sale__created_at") if filter_type == "monthly" else TruncMonth("sale__created_at")

        data = (
            items_qs
            .annotate(period=trunc)
            .values("period")
            .annotate(
                profit=Sum(
                    ExpressionWrapper(
                        (F("unit_price") - F("purchase_price")) * F("quantity"),
                        output_field=DecimalField()
                    )
                )
            )
            .order_by("period")
        )

        return {
            "labels": [i["period"].strftime("%Y-%m-%d") for i in data],
            "data": [i["profit"] for i in data]
        }



class ProfitChartService:

    @staticmethod
    def get(items_qs, filter_type):

        if filter_type == "monthly":
            trunc = TruncWeek("sale__created_at")
        else:
            trunc = TruncMonth("sale__created_at")

        data = (
            items_qs
            .annotate(period=trunc)
            .values("period")
            .annotate(
                profit=Sum(
                    ExpressionWrapper(
                        (F("unit_price") - F("purchase_price")) * F("quantity"),
                        output_field=DecimalField()
                    )
                )
            )
            .order_by("period")
        )

        return data
