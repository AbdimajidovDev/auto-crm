from django.db.models import Q
from drf_spectacular.utils import extend_schema

from apps.common.excel_export import BaseExcelExportAPIView
from apps.transfer.models import StockTransfer


@extend_schema(
    tags=["Transfer"],
    summary="O'tkazmalarni Excel (.xlsx) ga eksport qilish (?search=&status=&date_from=&date_to=)",
)
class TransferExportAPIView(BaseExcelExportAPIView):
    filename = "otkazmalar"
    date_field = "created_at"

    def get_queryset(self, request):
        qs = (
            StockTransfer.objects
            .select_related("from_store", "to_store", "approved_by")
            .prefetch_related("items__product")
            .order_by("-created_at")
        )

        # Ro'yxatdagi bilan bir xil qidiruv (ID / mahsulot / do'kon nomlari)
        search = (request.query_params.get("search") or "").strip()
        if search:
            query = (
                Q(items__product__name__icontains=search)
                | Q(items__product__sku__icontains=search)
                | Q(items__product__barcode__icontains=search)
                | Q(from_store__name__icontains=search)
                | Q(to_store__name__icontains=search)
            )
            if search.isdigit():
                query |= Q(id=int(search)) | Q(items__product__id=int(search))
            qs = qs.filter(query).distinct()

        status = request.query_params.get("status")
        if status in dict(StockTransfer.Status.choices):
            qs = qs.filter(status=status)

        return qs

    def get_sheets(self, request, queryset):
        transfer_columns = [
            ("ID", 8),
            ("Sana", 17),
            ("Qayerdan", 22),
            ("Qayerga", 22),
            ("Holat", 14),
            ("Tasdiqlagan", 24),
            ("Tasdiqlangan vaqt", 17),
            ("Mahsulot xillari", 14),
            ("Jami dona", 12),
        ]
        item_columns = [
            ("O'tkazma ID", 12),
            ("Sana", 17),
            ("Mahsulot", 40),
            ("SKU", 14),
            ("Miqdor", 10),
            ("Olish narxi", 14),
            ("Sotish narxi", 14),
        ]

        transfers = list(queryset)

        def transfer_rows():
            for transfer in transfers:
                items = list(transfer.items.all())
                yield [
                    transfer.id,
                    transfer.created_at,
                    transfer.from_store.name if transfer.from_store else "",
                    transfer.to_store.name if transfer.to_store else "",
                    transfer.get_status_display(),
                    transfer.approved_by.full_name if transfer.approved_by else "",
                    transfer.approved_at,
                    len(items),
                    sum(item.quantity for item in items),
                ]

        def item_rows():
            for transfer in transfers:
                for item in transfer.items.all():
                    yield [
                        transfer.id,
                        transfer.created_at,
                        item.product.name if item.product else "",
                        item.product.sku if item.product else "",
                        item.quantity,
                        item.purchase_price,
                        item.selling_price,
                    ]

        return [
            ("O'tkazmalar", transfer_columns, transfer_rows()),
            ("Mahsulotlar", item_columns, item_rows()),
        ]
