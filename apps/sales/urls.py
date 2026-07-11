from django.urls import path
from apps.sales.views import (
    SaleCreateAPIView,
    SaleListAPIView,
    SaleDetailAPIView,
    CustomerDebtListAPIView,
    SaleReturnCreateAPIView,
    SaleReturnListAPIView,
    BankCardListCreateAPIView,
    BankCardDetailAPIView,
)


urlpatterns = [
    path('list/', SaleListAPIView.as_view()),
    path('create/', SaleCreateAPIView.as_view()),

    path('bank-cards/', BankCardListCreateAPIView.as_view()),
    path('bank-cards/<int:pk>/', BankCardDetailAPIView.as_view()),

    path('debtor-customers/', CustomerDebtListAPIView.as_view()),

    path('sale-return/list/', SaleReturnListAPIView.as_view()),
    path('sale-return/', SaleReturnCreateAPIView.as_view()),

    path('<int:pk>/', SaleDetailAPIView.as_view()),
]
