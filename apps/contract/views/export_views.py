from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema

from apps.common.excel_export import BaseExcelExportAPIView
from apps.contract.filters import StockEntryFilter
from apps.contract.models import StockEntry, StockEntryItem
from apps.contract.views.supplier_crud_view import _supplier_queryset


@extend_schema(
    tags=["Stock Entry"],
    summary="Kirimlarni Excel (.xlsx) ga eksport qilish "
            "(?date_from=&date_to=&store=&supplier= — ro'yxat filtrlari bilan bir xil)",
)
class StockEntryExportAPIView(BaseExcelExportAPIView):
    filename = "kirimlar"
    # Sana filtri StockEntryFilter (date_from/date_to) orqali qo'llanadi
    date_field = None

    def get_queryset(self, request):
        qs = (
            StockEntry.objects
            .select_related("supplier", "store", "created_by")
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=StockEntryItem.objects.select_related("product"),
                ),
            )
            .order_by("-created_at")
        )
        qs = StockEntryFilter(request.query_params, queryset=qs).qs
        # Ro'yxatdagi SearchFilter bilan bir xil: ta'minotchi nomi bo'yicha
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(supplier__name__icontains=search)
        return qs

    def get_sheets(self, request, queryset):
        entry_columns = [
            ("ID", 8),
            ("Sana", 17),
            ("Ta'minotchi", 26),
            ("Do'kon", 20),
            ("Jami summa", 14),
            ("Naqd", 14),
            ("Karta", 14),
            ("To'langan", 14),
            ("Qarz", 14),
            ("Yaratdi", 24),
            ("Mahsulot xillari", 14),
            ("Jami dona", 12),
        ]
        item_columns = [
            ("Kirim ID", 10),
            ("Sana", 17),
            ("Mahsulot", 40),
            ("SKU", 14),
            ("Miqdor", 10),
            ("Olish narxi", 14),
            ("Sotish narxi", 14),
        ]

        entries = list(queryset)

        def entry_rows():
            for entry in entries:
                items = list(entry.items.all())
                yield [
                    entry.id,
                    entry.created_at,
                    entry.supplier.name if entry.supplier else "",
                    entry.store.name if entry.store else "",
                    entry.total_amount,
                    entry.cash_amount,
                    entry.card_amount,
                    entry.paid_amount,
                    entry.debt_amount,
                    entry.created_by.full_name if entry.created_by else "",
                    len(items),
                    sum(item.quantity for item in items),
                ]

        def item_rows():
            for entry in entries:
                for item in entry.items.all():
                    yield [
                        entry.id,
                        entry.created_at,
                        item.product.name if item.product else "",
                        item.product.sku if item.product else "",
                        item.quantity,
                        item.purchase_price,
                        item.selling_price,
                    ]

        return [
            ("Kirimlar", entry_columns, entry_rows()),
            ("Mahsulotlar", item_columns, item_rows()),
        ]


@extend_schema(
    tags=["Supplier"],
    summary="Ta'minotchilarni Excel (.xlsx) ga eksport qilish "
            "(?search=&is_active=&has_debt=&ordering=&date_from=&date_to=)",
)
class SupplierExportAPIView(BaseExcelExportAPIView):
    filename = "taminotchilar"
    date_field = "created_at"

    # Saralash faqat ruxsat etilgan (annotatsiyalangan) maydonlar bo'yicha
    ALLOWED_ORDERINGS = {
        "name",
        "-total_purchase_amount",
        "-total_debt",
        "-created_at",
    }

    def get_queryset(self, request):
        # Ro'yxat bilan bir xil queryset: search/is_active + jami xarid va qarz annotatsiyalari.
        # `only()` qayta chaqirildi: bazadagi to'plamga `created_at` ham kiritiladi,
        # aks holda har qator uchun deferred-field so'rovi (N+1) chiqadi.
        qs = (
            _supplier_queryset(
                search=request.query_params.get("search"),
                is_active=request.query_params.get("is_active"),
            )
            .only(
                "id", "name", "description", "address",
                "phone_number", "inn", "is_active", "created_at",
            )
        )

        # Faqat qarzdor ta'minotchilar
        if request.query_params.get("has_debt") in ("true", "1"):
            qs = qs.filter(total_debt__gt=0)

        ordering = request.query_params.get("ordering")
        if ordering not in self.ALLOWED_ORDERINGS:
            ordering = "name"
        return qs.order_by(ordering)

    def get_sheets(self, request, queryset):
        columns = [
            ("ID", 8),
            ("Nomi", 30),
            ("Telefon", 16),
            ("INN", 14),
            ("Manzil", 30),
            ("Jami xarid", 16),
            ("Qarz", 16),
            ("Faol", 8),
            ("Qo'shilgan sana", 17),
        ]

        def rows():
            for supplier in queryset:
                yield [
                    supplier.id,
                    supplier.name,
                    supplier.phone_number,
                    supplier.inn,
                    supplier.address,
                    supplier.total_purchase_amount,
                    supplier.total_debt,
                    "Ha" if supplier.is_active else "Yo'q",
                    supplier.created_at,
                ]

        return [("Ta'minotchilar", columns, rows())]
