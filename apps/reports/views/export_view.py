from datetime import datetime

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from apps.products.models import Product
from apps.products.services.product_query_service import (
    LOW_STOCK_THRESHOLD,
    annotate_stock_qty,
)
from apps.reports.permissions import scope_report_params
from apps.reports.services.excel_export_service import ExcelExportService
from apps.reports.services.report_service import ReportFilterService, ReportService
from apps.sales.models import Sale
from apps.store.models import Store

PAYMENT_TYPE_LABELS = {"cash": "Naqd", "card": "Karta", "mixed": "Aralash", "debt": "Qarz"}


def _low_stock_block(store_id) -> list[dict]:
    """Bosh sahifadagi 'Kam qoldiq' blokiga mos ro'yxat (eng kam qolganlar birinchi)."""
    qs = Product.objects.filter(status=Product.ProductStatus.ACTIVE).select_related("category")
    qs = annotate_stock_qty(qs, store_id if store_id else None)
    qs = qs.filter(stock_qty__lte=LOW_STOCK_THRESHOLD).order_by("stock_qty", "name")[:200]
    return [
        {
            "name": p.name,
            "sku": p.sku or "-",
            "category": p.category.name if p.category else "-",
            "stock": p.stock_qty or 0,
            "min_stock": p.min_stock or 0,
        }
        for p in qs
    ]


def _recent_sales_block(store_id) -> list[dict]:
    """Bosh sahifadagi 'Oxirgi sotuvlar' blokiga mos ro'yxat (oxirgi 20 ta)."""
    qs = Sale.objects.select_related("store", "customer").order_by("-created_at")
    if store_id:
        qs = qs.filter(store_id=store_id)
    return [
        {
            "id": s.id,
            "date": timezone.localtime(s.created_at).strftime("%d.%m.%Y %H:%M"),
            "store": s.store.name if s.store else "-",
            "customer": s.customer.full_name if s.customer else "-",
            "total": str(s.total_amount or 0),
            "payment": PAYMENT_TYPE_LABELS.get(s.payment_type, s.payment_type),
        }
        for s in qs[:20]
    ]

FILTER_LABELS = {
    "daily":   "Kunlik",
    "weekly":  "Haftalik",
    "monthly": "Oylik",
    "yearly":  "Yillik",
}


@extend_schema(
    tags=['Reports'],
    summary='Hisobotni Excel formatda yuklash (dashboard, diagrammalar bilan).',
)
class ReportsExcelExportAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # report_view.py bilan bir xil chaqiruv — sahifadagi filtrlar
        # (filter/store_id/from/to) aynan qo'llanadi.
        # Do'kon admini faqat O'Z do'koni bo'yicha yuklaydi — boshqa do'kon
        # yoki 'all' (umumiy) so'ralsa ham server o'z do'koniga majburlaydi;
        # superadmin istalgan do'kon/sklad yoki umumiy hisobotni yuklaydi.
        params = scope_report_params(request)
        data = ReportService.get(params)

        # Excel sarlavhasi uchun meta: davr, do'kon, yaratilgan vaqt
        filter_type = params.get("filter", "monthly")
        date_from, date_to = ReportFilterService.resolve_dates(
            filter_type, params.get("from"), params.get("to")
        )
        store_id = ReportFilterService.resolve_store(params.get("store_id"))
        store_name = "Barcha do'konlar"
        if store_id:
            store_name = (
                Store.objects.filter(id=store_id).values_list("name", flat=True).first()
                or f"Do'kon #{store_id}"
            )

        period = f"{date_from:%d.%m.%Y} — {date_to:%d.%m.%Y}"
        label = FILTER_LABELS.get(filter_type)
        if label and not (params.get("from") and params.get("to")):
            period = f"{label} ({period})"

        meta = {
            "period": period,
            "store": store_name,
            "generated": timezone.localtime().strftime("%d.%m.%Y %H:%M"),
        }

        # Bo'limlab eksport: ?section=top_products|customer_debts|... — faqat
        # shu bo'lim varag'i yuklanadi. Berilmasa (yoki noto'g'ri) — to'liq hisobot.
        section = params.get("section")
        sections = {section} if section in ExcelExportService.SECTIONS else None

        # Bosh sahifa bloklariga mos qo'shimcha varaqlar (kam qoldiq, oxirgi
        # sotuvlar) — kerak bo'lgandagina hisoblanadi
        need_low = sections is None or "low_stock" in sections
        need_recent = sections is None or "recent_sales" in sections
        if need_low:
            data = {**data, "lowStock": _low_stock_block(store_id)}
        if need_recent:
            data = {**data, "recentSales": _recent_sales_block(store_id)}

        file = ExcelExportService.generate_report(data, meta, sections=sections)

        response = HttpResponse(
            file,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

        section_part = f"{section}_" if sections else ""
        filename = f"report_{section_part}{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

        response['Content-Disposition'] = f'attachment; filename={filename}'

        return response
