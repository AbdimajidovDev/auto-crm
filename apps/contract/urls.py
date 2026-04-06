from django.urls import path

from apps.contract.views import (
    SupplierCreateAPIView,
    SupplierDetailAPIView,
    SupplierListAPIView,
)



urlpatterns = [
    path('supplier/', SupplierListAPIView.as_view()),
    path('supplier/create/', SupplierCreateAPIView.as_view()),
    path('supplier/<int:pk>/', SupplierDetailAPIView.as_view()),
]