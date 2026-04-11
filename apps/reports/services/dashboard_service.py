from django.db.models import Sum, Q
from django.utils import timezone
from apps.sales.models import Sale
from apps.debts.models import CustomerDebt
from apps.contract.models import SupplierTransaction
from apps.products.models import Product
from django.db.models import Sum, F, Case, When, Value, DecimalField

class ReportService:
    @staticmethod
    def get_dashboard_data():
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Barcha hisoblashlar baza darajasida
        data = {
            "total_products_in_stock": Product.objects.count() or 0,

            "monthly_revenue": Sale.objects.filter(created_at__gte=start_of_month).aggregate(
                total=Sum('paid_amount')
            )['total'] or 0,

            "total_customer_debt": CustomerDebt.objects.aggregate(
                debt=Sum(
                    Case(
                        When(type=CustomerDebt.Type.INCREASE, then=F('amount')),
                        When(type=CustomerDebt.Type.DECREASE, then=-F('amount')),
                        default=Value(0),
                        output_field=DecimalField()
                    )
                )
            )['debt'] or 0,
            "total_supplier_debt": SupplierTransaction.objects.aggregate(
                debt=Sum(
                    Case(
                        When(type=SupplierTransaction.TransactionType.INVENTORY_IN, then=F('amount')),
                        When(type=SupplierTransaction.TransactionType.PAYMENT, then=-F('amount')),
                        default=Value(0),
                        output_field=DecimalField()
                    )
                )
            )['debt'] or 0,

            "report_date": now
        }
        return data
