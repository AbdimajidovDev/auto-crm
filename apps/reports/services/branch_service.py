from django.db.models import Sum, Count
from apps.sales.models import Sale


class BranchService:

    @staticmethod
    def get(date_from, date_to, store_ids):

        qs = Sale.objects.filter(created_at__range=(date_from, date_to))

        if store_ids:
            qs = qs.filter(store_id__in=store_ids)

        return list(
            qs.values("store_id", "store__name")
            .annotate(
                revenue=Sum("total_amount"),
                orders=Count("id"),
                customers=Count("customer", distinct=True),
            )
        )
