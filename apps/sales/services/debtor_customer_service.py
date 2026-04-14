from django.db.models import Sum, Case, When, DecimalField, F

from apps.debts.models import CustomerDebt

from django.db.models import Sum, Case, When, DecimalField, F

from apps.debts.models import CustomerDebt


class CustomerDebtService:

    @staticmethod
    def get_customer_debts(*, user):
        qs = CustomerDebt.objects.select_related("customer", "sale")

        qs = qs.filter(sale__isnull=False)

        # 🔐 ROLE LOGIC
        if user.is_superuser or getattr(user, "is_sklad", False):
            pass
        else:
            qs = qs.filter(sale__seller=user)

        # 🔥 AGGREGATION (store qo‘shildi)
        qs = qs.values(
            "customer_id",
            "customer__full_name",
            "sale__store_id"
        ).annotate(
            total_debt=Sum(
                Case(
                    When(type="i", then=F("amount")),
                    When(type="d", then=-F("amount")),
                    output_field=DecimalField(max_digits=20, decimal_places=2)
                )
            )
        ).filter(total_debt__gt=0)

        return qs

    @staticmethod
    def format_debt_response(*, data):
        result = []

        for item in data:
            result.append({
                "customer_id": item["customer_id"],
                "customer_name": f"{item['customer__full_name']}",
                "store_id": item["sale__store_id"],
                "total_debt": item["total_debt"]
            })

        return result