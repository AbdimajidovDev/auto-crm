from django.urls import path
from apps.reports.views import DashboardAPIView
from apps.reports.views.export_view import ReportsExcelExportAPIView
from apps.reports.views.report_view import ReportsAPIView
from apps.reports.views.top_product_view import TopProductsAPIView

urlpatterns = [
    # Dashboard reports
    path('dashboard/', DashboardAPIView.as_view()),
    path('top-products/', TopProductsAPIView.as_view()),

    # Reports
    path('', ReportsAPIView.as_view()),

    # Export
    path("export/", ReportsExcelExportAPIView.as_view())
]