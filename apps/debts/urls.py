from django.urls import path
from apps.debts.views import (
    CustomerPayDebtAPIView,
    CustomerPaymentsAPIView,
    PayDebtAPIView,
    PayDebtListAPIView,
    PayDebtDetailAPIView,
)

urlpatterns = [
    path('list/', PayDebtListAPIView.as_view()),
    path('create/', PayDebtAPIView.as_view()),
    # Mijoz bo'yicha: FIFO qarz to'lash va to'lovlar tarixi
    path('customer/pay/', CustomerPayDebtAPIView.as_view()),
    path('customer/<int:customer_id>/payments/', CustomerPaymentsAPIView.as_view()),
    path('<int:pk>/', PayDebtDetailAPIView.as_view()),
]