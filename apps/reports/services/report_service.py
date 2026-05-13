from apps.reports.utils.date_parser import DateValidator
from .summary_service import SummaryService
from .branch_service import BranchService
from .chart_service import ChartService
from .product_service import ProductService
from .debt_service import DebtService
from .filter_service import ReportFilterService
from apps.sales.models import SaleItem
from apps.reports.utils.date_filters import DateRangeResolver


# ===========================   Reports   ==================================================


class ReportService:

    @staticmethod
    def get(user, params):

        store_ids = ReportFilterService.resolve_store(params.get("storeId"))

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
        # ⚠️ MUAMMO [PERFORMANCE]: `items_qs` `select_related("sale")` bilan yengillashtirilmagan.
        # Sabab: keyingi chart/service hisoblar sale sanasi va store filteriga tayanadi.
        # Natija: service kengaysa sale FK bo'yicha qo'shimcha querylar paydo bo'lishi mumkin.
        # ✅ YECHIM:
        # items_qs = SaleItem.objects.select_related("sale", "product").filter(
        #     sale__created_at__range=(date_from, date_to)
        # )
        # N+1 / ma'lumot: keyingi `ChartService.profit_trend` va boshqa hisoblar `purchase_price`ga
        # tayangan — NULL bo'lsa natijalar buziladi; `select_related("sale")` ixtiyoriy yengillashtirish.

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


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Report item querysetini select_related bilan barqaror qilish]
# ═══════════════════════════════


# ===================================================================================================
# ---------------------------------------------------------------------------------------------------
# ===================================================================================================
