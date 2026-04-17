from django.urls import path
from apps.reports.views import DashboardReportAPIView, DashboardAPIView
from apps.reports.views.top_product_view import TopProductsAPIView

urlpatterns = [
    # path('', DashboardReportAPIView.as_view()),
    path('', DashboardAPIView.as_view()),
    path('top-products/', TopProductsAPIView.as_view()),
]