from django.urls import path
from apps.reports.views import DashboardAPIView
from apps.reports.views.export_view import ReportsExcelExportAPIView
from apps.reports.views.report_builder_view import (
    ReportBuilderExportAPIView,
    ReportBuilderGenerateAPIView,
    ReportBuilderMetaAPIView,
)
from apps.reports.views.report_view import ReportsAPIView
from apps.reports.views.top_product_view import TopProductsAPIView

urlpatterns = [
    # Dashboard reports
    path('dashboard/', DashboardAPIView.as_view()),
    path('top-products/', TopProductsAPIView.as_view()),

    # Reports (statistika bloklari — dashboard/eski interfeys uchun)
    path('', ReportsAPIView.as_view()),

    # Reports moduli: filtr → generate → jadval → eksport
    path('builder/meta/', ReportBuilderMetaAPIView.as_view()),
    path('builder/', ReportBuilderGenerateAPIView.as_view()),
    path('builder/export/', ReportBuilderExportAPIView.as_view()),

    # Export (to'liq statistika workbook — dashboard uchun)
    path("export/", ReportsExcelExportAPIView.as_view())
]