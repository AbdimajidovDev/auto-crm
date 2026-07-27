from django.urls import path

from apps.contract.views import (
    SupplierCreateAPIView,
    SupplierDetailAPIView,
    SupplierListAPIView,
    StockEntryCreateAPIView,
    StockEntryListAPIView,
    StockEntryImportAPIView,
    StockEntryImportAnalyzeAPIView,
    StockEntryImportTemplateAPIView,
    StockEntryReturnCreateAPIView,
    StockEntryReturnsByEntryAPIView,
    StockEntryReturnListAPIView,
    StockEntryReturnExportAPIView,
    PurchaseSessionListCreateAPIView,
    PurchaseSessionDetailAPIView,
    PurchaseSessionReceiveAPIView,
    PurchaseSessionConfirmAPIView,
    SupplierStatsAPIView,
    SupplierPaymentsBySupplierAPIView,
    SupplierProductsAPIView,
)
from apps.contract.views.supplier_payment_view import SupplierPaymentAPIView, SupplierPaymentListAPIView
from apps.contract.views.export_views import StockEntryExportAPIView, SupplierExportAPIView

urlpatterns = [
    # Ta'minotchi
    path('supplier/', SupplierListAPIView.as_view()),
    path('supplier/export/', SupplierExportAPIView.as_view()),
    path('supplier/create/', SupplierCreateAPIView.as_view()),
    path('supplier/<int:pk>/', SupplierDetailAPIView.as_view()),

    # Ta'minotchi detail sahifasi tablari (dashboard / to'lovlar / tovarlar)
    path('supplier/<int:pk>/stats/', SupplierStatsAPIView.as_view()),
    path('supplier/<int:pk>/payments/', SupplierPaymentsBySupplierAPIView.as_view()),
    path('supplier/<int:pk>/products/', SupplierProductsAPIView.as_view()),

    # Kirim
    path("entry/list/", StockEntryListAPIView.as_view()),
    path("entry/export/", StockEntryExportAPIView.as_view()),
    path("entry/create/", StockEntryCreateAPIView.as_view()),

    # Kirim sessiyasi (progressiv wizard: qoralama → qabul → tasdiqlash)
    path("entry/session/", PurchaseSessionListCreateAPIView.as_view()),
    path("entry/session/<int:pk>/", PurchaseSessionDetailAPIView.as_view()),
    path("entry/session/<int:pk>/receive/", PurchaseSessionReceiveAPIView.as_view()),
    path("entry/session/<int:pk>/confirm/", PurchaseSessionConfirmAPIView.as_view()),

    # Kirim — Excel import
    path("entry/import/", StockEntryImportAPIView.as_view()),
    path("entry/import/analyze/", StockEntryImportAnalyzeAPIView.as_view()),
    path("entry/import/template/", StockEntryImportTemplateAPIView.as_view()),

    # Kirim — qaytimlar (ta'minotchiga mahsulot qaytarish)
    path("entry/returns/", StockEntryReturnListAPIView.as_view()),
    path("entry/returns/export/", StockEntryReturnExportAPIView.as_view()),
    path("entry/<int:entry_id>/return/", StockEntryReturnCreateAPIView.as_view()),
    path("entry/<int:entry_id>/returns/", StockEntryReturnsByEntryAPIView.as_view()),

    path("supplier-payments/create/", SupplierPaymentAPIView.as_view()),
    path("supplier-payments/<int:entry_id>/", SupplierPaymentListAPIView.as_view()),
]