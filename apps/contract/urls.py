from django.urls import path

from apps.contract.views import (
    SupplierCreateAPIView,
    SupplierDetailAPIView,
    SupplierListAPIView, StockEntryCreateAPIView, StockEntryListAPIView,
)



urlpatterns = [
    # Ta'minotchi
    path('supplier/', SupplierListAPIView.as_view()),
    path('supplier/create/', SupplierCreateAPIView.as_view()),
    path('supplier/<int:pk>/', SupplierDetailAPIView.as_view()),

    # Kirim
    path("entry/list/", StockEntryListAPIView.as_view()),
    path("entry/create/", StockEntryCreateAPIView.as_view()),
]