from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema

from apps.common.excel_export import BaseExcelExportAPIView
from apps.sales.filters import SaleFilter
from apps.sales.models import Sale, SaleItem


@extend_schema(
    tags=["Sales"],
    summary="Sotuvlarni Excel (.xlsx) ga eksport qilish "
            "(?date_from=&date_to=&store=&status= — ro'yxat filtrlari bilan bir xil)",
)
class SaleExportAPIView(BaseExcelExportAPIView):
    filename = "sotuvlar"
    # Sana filtri SaleFilter (date_from/date_to) orqali qo'llanadi
    date_field = None

    def get_queryset(self, request):
        qs = (
            Sale.objects
            .select_related("store", "customer", "seller")
            .prefetch_related(
                Prefetch("items", queryset=SaleItem.objects.select_related("product")),
            )
            .order_by("-created_at")
        )
        # Ro'yxat view'i bilan bir xil scoping: oddiy foydalanuvchi faqat o'z sotuvlarini oladi
        if not request.user.is_superuser:
            qs = qs.filter(seller=request.user)
        # store/customer/seller/status/date_from/date_to — ro'yxatdagi filterset qayta ishlatiladi
        qs = SaleFilter(request.query_params, queryset=qs).qs
        # Ro'yxatdagi SearchFilter bilan bir xil: mijoz ismi bo'yicha
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(customer__full_name__icontains=search)
        return qs

    def get_sheets(self, request, queryset):
        sale_columns = [
            ("ID", 8),
            ("Sana", 17),
            ("Do'kon", 20),
            ("Mijoz", 24),
            ("Sotuvchi", 24),
            ("Holat", 12),
            ("To'lov turi", 12),
            ("Jami summa", 14),
            ("To'langan", 14),
            ("Qarz", 12),
            ("Chegirma", 12),
            ("Mahsulot xillari", 14),
            ("Jami dona", 12),
        ]
        item_columns = [
            ("Sotuv ID", 10),
            ("Sana", 17),
            ("Mahsulot", 40),
            ("SKU", 14),
            ("Miqdor", 10),
            ("Narx", 14),
            ("Jami", 14),
        ]

        sales = list(queryset)

        def sale_rows():
            for sale in sales:
                items = list(sale.items.all())
                debt = (sale.total_amount or 0) - (sale.paid_amount or 0)
                yield [
                    sale.id,
                    sale.created_at,
                    sale.store.name if sale.store else "",
                    sale.customer.full_name if sale.customer else "",
                    sale.seller.full_name if sale.seller else "",
                    sale.get_status_display(),
                    sale.get_payment_type_display() if sale.payment_type else "",
                    sale.total_amount,
                    sale.paid_amount,
                    debt if debt > 0 else 0,
                    sale.discount_amount,
                    len(items),
                    sum(item.quantity for item in items),
                ]

        def item_rows():
            for sale in sales:
                for item in sale.items.all():
                    yield [
                        sale.id,
                        sale.created_at,
                        item.product.name if item.product else "",
                        item.product.sku if item.product else "",
                        item.quantity,
                        item.unit_price,
                        item.total_price,
                    ]

        return [
            ("Sotuvlar", sale_columns, sale_rows()),
            ("Mahsulotlar", item_columns, item_rows()),
        ]
