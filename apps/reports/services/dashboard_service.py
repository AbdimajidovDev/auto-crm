from django.db.models import Sum, Q
from django.utils import timezone

from apps.reports.services.store_scope_service import StoreScopeService
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


# services/dashboard_service.py

from django.db.models import Sum, F, DecimalField, ExpressionWrapper
from apps.sales.models import Sale
from apps.contract.models import SupplierTransaction
from apps.products.models import ProductBatch


class DashboardService:

    @staticmethod
    def get_reports(user, date_from, date_to):

        store_ids = StoreScopeService.get_user_stores(user)

        sales = Sale.objects.filter(
            created_at__range=(date_from, date_to)
        )

        if store_ids is not None:
            sales = sales.filter(store_id__in=store_ids)

        # 🔥 AGGREGATIONS
        totals = sales.aggregate(
            total_revenue=Sum("total_amount"),
            total_paid=Sum("paid_amount"),
        )

        total_revenue = totals["total_revenue"] or 0
        total_paid = totals["total_paid"] or 0

        total_debt = total_revenue - total_paid

        # Supplier debt
        supplier = SupplierTransaction.objects.all()

        if store_ids is not None:
            supplier = supplier.filter(entry__store_id__in=store_ids)

        supplier_totals = supplier.aggregate(
            total_in=Sum("amount", filter=Q(type="in")),
            total_paid=Sum("amount", filter=Q(type="pay")),
        )

        supplier_debt = (supplier_totals["total_in"] or 0) - (supplier_totals["total_paid"] or 0)

        # Product count
        products = ProductBatch.objects.all()
        if store_ids is not None:
            products = products.filter(store_id__in=store_ids)

        total_products = products.count()

        return {
            "totalProducts": total_products,
            "totalRevenue": total_revenue,
            "totalPaid": total_paid,
            "totalDebt": total_debt,
            "supplierDebt": supplier_debt,
        }
