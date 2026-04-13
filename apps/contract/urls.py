from django.urls import path

from apps.contract.views import (
    SupplierCreateAPIView,
    SupplierDetailAPIView,
    SupplierListAPIView,
    StockEntryCreateAPIView,
    StockEntryListAPIView,
)
from apps.contract.views.supplier_payment_view import SupplierPaymentAPIView, SupplierPaymentListAPIView

urlpatterns = [
    # Ta'minotchi
    path('supplier/', SupplierListAPIView.as_view()),
    path('supplier/create/', SupplierCreateAPIView.as_view()),
    path('supplier/<int:pk>/', SupplierDetailAPIView.as_view()),

    # Kirim
    path("entry/list/", StockEntryListAPIView.as_view()),
    path("entry/create/", StockEntryCreateAPIView.as_view()),
    path("supplier-payments/create/", SupplierPaymentAPIView.as_view()),
    path("supplier-payments/<int:entry_id>/", SupplierPaymentListAPIView.as_view()),
]