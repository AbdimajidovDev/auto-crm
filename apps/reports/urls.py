from django.urls import path
from apps.reports.views import DashboardReportAPIView


urlpatterns = [
    path('', DashboardReportAPIView.as_view()),
]