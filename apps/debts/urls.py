from django.urls import path
from apps.debts.views import PayDebtAPIView, PayDebtListAPIView

urlpatterns = [
    path('list/', PayDebtListAPIView.as_view()),
    path('create/', PayDebtAPIView.as_view()),
]