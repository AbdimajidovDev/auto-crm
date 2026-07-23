from drf_spectacular.utils import extend_schema

from apps.common.excel_export import BaseExcelExportAPIView
from apps.inventory.models import InventorySession
from apps.inventory.services.low_stock_service import LowStockService


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


@extend_schema(
    tags=["Low Stock"],
    summary=(
        "Kam qolgan mahsulotlarni Excel (.xlsx) ga eksport qilish "
        "(?action_type=purchase|transfer&store=&keys=storeId-productId,...)"
    ),
)
class LowStockExportAPIView(BaseExcelExportAPIView):
    """
    Ro'yxat bilan BIR XIL jonli hisob (LowStockService.compute_live) eksport
    qilinadi. `keys` berilsa (checkbox bilan tanlanganlar) — faqat o'sha
    qatorlar tushadi; bo'lmasa joriy filtrlar bo'yicha to'liq ro'yxat.
    """

    filename = "kam_qolgan_mahsulotlar"
    date_field = None  # jonli qoldiq hisobida sana filtri ma'nosiz

    def get_queryset(self, request):
        qp = request.query_params
        results = LowStockService.compute_live(
            store_id=qp.get("store"),
            action_type=qp.get("action_type"),
            search=qp.get("search"),
        )
        keys = (qp.get("keys") or "").strip()
        if keys:
            wanted = {k.strip() for k in keys.split(",") if k.strip()}
            results = [r for r in results if r["id"] in wanted]
        return results

    def get_sheets(self, request, rows_data):
        columns = [
            ("Mahsulot", 36),
            ("SKU", 16),
            ("Do'kon", 22),
            ("Qoldiq", 10),
            ("Minimal", 10),
            ("Amal", 16),
            ("Boshqa do'konlarda (transfer manbalari)", 44),
        ]

        def rows():
            for r in rows_data:
                yield [
                    r["product_name"],
                    r["sku"],
                    r["store_name"],
                    r["current_quantity"],
                    r["min_stock"],
                    "Xarid kerak" if r["action_type"] == "purchase" else "Transfer kerak",
                    "; ".join(
                        f"{s['store_name']}: {s['quantity']}" for s in r["sources"]
                    ) or "—",
                ]

        return [("Kam qolgan", columns, rows())]
