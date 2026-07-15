from drf_spectacular.utils import extend_schema

from apps.common.excel_export import BaseExcelExportAPIView
from apps.inventory.models import InventorySession


@extend_schema(
    tags=["Inventory"],
    summary="Inventarizatsiya sessiyalarini Excel (.xlsx) ga eksport qilish (?date_from=&date_to=)",
)
class InventoryExportAPIView(BaseExcelExportAPIView):
    filename = "inventarizatsiya"
    date_field = "created_at"

    def get_queryset(self, request):
        qs = (
            InventorySession.objects
            .select_related("store", "started_by")
            .order_by("-started_at")
        )
        # Ro'yxat view'i bilan bir xil scoping: oddiy foydalanuvchi faqat o'z do'konini ko'radi
        if not request.user.is_superuser:
            qs = qs.filter(
                store__user_links__user=request.user,
                store__user_links__is_active=True,
            )
        return qs

    def get_sheets(self, request, queryset):
        columns = [
            ("ID", 8),
            ("Do'kon", 24),
            ("Boshlagan", 26),
            ("Holat", 14),
            ("Boshlangan vaqt", 17),
            ("Yaratilgan", 17),
        ]

        def rows():
            for session in queryset:
                yield [
                    session.id,
                    session.store.name if session.store else "",
                    session.started_by.full_name if session.started_by else "",
                    session.get_status_display(),
                    session.started_at,
                    session.created_at,
                ]

        return [("Inventarizatsiya", columns, rows())]
