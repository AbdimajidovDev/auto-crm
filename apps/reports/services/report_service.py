from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField

from apps.debts.models import CustomerDebt
from apps.reports.services.store_scope_service import ReportStoreScope
from apps.reports.services.top_product_service import TopProductsService
from apps.reports.utils.date_parser import DateValidator
from apps.sales.models import Sale, SaleItem






# ===========================   Reports   ==================================================

# class ReportService:
#
#     @staticmethod
#     def get_full_report(user, params):
#
#         store_ids = ReportStoreScope.resolve(params.get("branchId"))
#
#         date_from, date_to = DateValidator.validate(
#             params.get("from"),
#             params.get("to")
#         )
#
#         summary = SummaryService.get(date_from, date_to, store_ids)
#
#         branches = BranchService.get(date_from, date_to, store_ids)
#
#         top_products = TopProductsService.get_top_products(
#             user=user,
#             date_from=date_from,
#             date_to=date_to,
#             limit=5,
#             store_id=params.get("branchId")
#         )
#
#         return {
#             "summary": summary,
#             "branchStatistics": branches,
#             "topSellingProducts": top_products,
#         }



# apps/reports/services/report_service.py

from .summary_service import SummaryService
from .branch_service import BranchService
from .chart_service import ChartService
from .product_service import ProductService
from .debt_service import DebtService
from .filter_service import ReportFilterService
from apps.sales.models import SaleItem
from ..utils.date_filters import DateRangeResolver


class ReportService:

    @staticmethod
    def get(user, params):

        # store_ids = ReportFilterService.resolve_store(params.get("branchId"))
        #
        # date_from, date_to = DateValidator.validate(
        #     params.get("from"),
        #     params.get("to")
        # )

        store_ids = ReportFilterService.resolve_store(params.get("branchId"))

        filter_type = params.get("filter", "monthly")

        date_from, date_to = DateValidator.validate(
            params.get("from"),
            params.get("to")
        )

        # 🔥 SHU YERGA
        if not date_from:
            date_from, date_to = DateRangeResolver.resolve(filter_type)

        summary = SummaryService.get(date_from, date_to, store_ids)

        branches = BranchService.get(date_from, date_to, store_ids)

        items_qs = SaleItem.objects.filter(
            sale__created_at__range=(date_from, date_to)
        )

        if store_ids:
            items_qs = items_qs.filter(sale__store_id__in=store_ids)

        chart = ChartService.profit_trend(items_qs, params.get("filter"))

        products = ProductService.top_products(date_from, date_to, store_ids)

        return {
            "filters": params,
            "summary": summary,
            "branchStatistics": branches,
            "charts": {
                "profitTrend": chart
            },
            "topSellingProducts": products,
            "debts": {
                "customerDebts": DebtService.customer_debt(store_ids),
                "supplierDebts": DebtService.supplier_debt()
            }
        }


# ===================================================================================================
# ---------------------------------------------------------------------------------------------------
# ===================================================================================================

