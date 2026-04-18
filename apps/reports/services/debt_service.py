from django.db.models import Sum, Q
from apps.debts.models import CustomerDebt
from apps.contract.models import SupplierTransaction


class DebtService:

    @staticmethod
    def customer_debt(store_ids):

        qs = CustomerDebt.objects.all()

        if store_ids:
            qs = qs.filter(sale__store_id__in=store_ids)

        data = qs.values("customer__full_name").annotate(
            inc=Sum("amount", filter=Q(type="i")),
            dec=Sum("amount", filter=Q(type="d")),
        )

        return [
            {
                "customerName": i["customer__full_name"],
                "debt": (i["inc"] or 0) - (i["dec"] or 0)
            }
            for i in data
        ]

    @staticmethod
    def supplier_debt():

        data = SupplierTransaction.objects.aggregate(
            inc=Sum("amount", filter=Q(type="in")),
            dec=Sum("amount", filter=Q(type="pay")),
        )

        return [{
            "supplierName": "All Suppliers",
            "debt": (data["inc"] or 0) - (data["dec"] or 0)
        }]
