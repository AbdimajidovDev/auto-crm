# services/chart_service.py

from django.db.models.functions import TruncDay, TruncWeek, TruncMonth
from django.db.models import Sum


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
