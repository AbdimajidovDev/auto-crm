from django.urls import path
from apps.reports.views import DashboardReportAPIView, DashboardAPIView
from apps.reports.views.report_view import ReportsAPIView
from apps.reports.views.top_product_view import TopProductsAPIView

urlpatterns = [
    # Dashboard reports
    # path('', DashboardReportAPIView.as_view()),
    path('dashboard/', DashboardAPIView.as_view()),
    path('top-products/', TopProductsAPIView.as_view()),

    # Reports
    path('', ReportsAPIView.as_view()),
]