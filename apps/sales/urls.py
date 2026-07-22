from django.urls import path
from apps.sales.views import (
    SaleCreateAPIView,
    SaleListAPIView,
    SaleDetailAPIView,
    SaleExportAPIView,
    SaleStatisticsAPIView,
    SaleBulkDeleteAPIView,
    SaleArchiveListAPIView,
    SaleRestoreAPIView,
    CustomerDebtListAPIView,
    SaleReturnCreateAPIView,
    SaleReturnListAPIView,
    BankCardListCreateAPIView,
    BankCardDetailAPIView,
)


urlpatterns = [
    path('list/', SaleListAPIView.as_view()),
    path('statistics/', SaleStatisticsAPIView.as_view()),
    path('export/', SaleExportAPIView.as_view()),
    path('create/', SaleCreateAPIView.as_view()),

    # Faqat superadmin: o'chirish (arxivga), arxiv ro'yxati, tiklash
    path('bulk-delete/', SaleBulkDeleteAPIView.as_view()),
    path('archive/', SaleArchiveListAPIView.as_view()),
    path('archive/restore/', SaleRestoreAPIView.as_view()),

    path('bank-cards/', BankCardListCreateAPIView.as_view()),
    path('bank-cards/<int:pk>/', BankCardDetailAPIView.as_view()),

    path('debtor-customers/', CustomerDebtListAPIView.as_view()),

    path('sale-return/list/', SaleReturnListAPIView.as_view()),
    path('sale-return/', SaleReturnCreateAPIView.as_view()),

    path('<int:pk>/', SaleDetailAPIView.as_view()),
]
