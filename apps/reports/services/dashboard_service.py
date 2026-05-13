from django.utils import timezone

from apps.reports.services.store_scope_service import StoreScopeService
from apps.debts.models import CustomerDebt
from apps.products.models import Product
from django.db.models import Q, Sum, F, Case, When, Value, DecimalField

from apps.sales.models import Sale
from apps.contract.models import SupplierTransaction
from apps.products.models import ProductBatch



class DashboardReportService:
    @staticmethod
    def get_dashboard_data():
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Barcha hisoblashlar baza darajasida
        data = {
            # ⚠️ MUAMMO [ARXITEKTURA]: KPI nomi stock miqdorini anglatadi, lekin Product katalog count ishlatilgan.
            # Sabab: `Product.objects.count()` ombordagi real quantity emas.
            # Natija: dashboardda noto'g'ri biznes metrika ko'rsatiladi.
            # ✅ YECHIM:
            # "total_products_in_stock": ProductBatch.objects.aggregate(total=Sum("quantity"))["total"] or 0
            # Eslatma: nom `total_products_in_stock` bo'lsa ham bu `Product` (katalog) soni —
            # ombordagi `ProductBatch` miqdori emas; KPI noto'g'ri talqin qilinishi mumkin.
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
        # ⚠️ MUAMMO [PERFORMANCE]: SupplierTransaction uchun avval `.all()` olinib, keyin scope filter qo'llanadi.
        # Sabab: kod mantiqan lazy bo'lsa ham query ni aniq boshlang'ich filter bilan ifodalash o'qilishi va indeks rejasini yaxshilaydi.
        # Natija: kelajakda `.all()` ustiga qo'shimcha ishlov qo'shilsa katta jadval xavfi oshadi.
        # ✅ YECHIM:
        # supplier = SupplierTransaction.objects.filter(entry__created_at__range=(date_from, date_to))
        # PERFORMANCE: butun `SupplierTransaction` jadvali yuklanadi — katta tarixda
        # `only()` / indekslangan filtr yoki alohida aggregate service yordamida cheklash foydali.
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


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 1
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Dashboard KPI `total_products_in_stock` ni ProductBatch quantity asosida hisoblash]
# ═══════════════════════════════
