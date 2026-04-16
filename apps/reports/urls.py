from django.urls import path
from apps.reports.views import DashboardReportAPIView, DashboardAPIView

urlpatterns = [
    # path('', DashboardReportAPIView.as_view()),
    path('', DashboardAPIView.as_view()),
]