from django.urls import path

from .views.low_stock_view import LowStockListAPIView, LowStockHistoryAPIView
from .views.export_view import InventoryExportAPIView, LowStockExportAPIView
from .views.inventory_count_view import InventoryOverCountView, InventoryShortCountView
from .views.inventory_view import (
    InventoryListAPIView,
    InventoryStartAPIView,
    InventorySetCountAPIView,
    InventoryFinalizeAPIView,
    InventoryCancelAPIView,
    InventoryMovementListView,
    InventoryDetailAPIView,
)



urlpatterns = [
    path('list/', InventoryListAPIView.as_view()),
    path('export/', InventoryExportAPIView.as_view()),
    path('list/<int:session_id>/', InventoryDetailAPIView.as_view()),
    path('movement-list/<int:session_id>/', InventoryMovementListView.as_view()),

    path('start/', InventoryStartAPIView.as_view()),
    path('scan/', InventorySetCountAPIView.as_view()),

    path('finalize/', InventoryFinalizeAPIView.as_view()),
    path('cancel/', InventoryCancelAPIView.as_view()),

    path("sessions/<int:session_id>/over/", InventoryOverCountView.as_view(), name="inventory-over-count"),
    path("sessions/<int:session_id>/short/", InventoryShortCountView.as_view(), name="inventory-short-count"),

    # Low stock monitoring
    path("low-stock/", LowStockListAPIView.as_view(), name="low-stock-list"),
    path("low-stock/history/", LowStockHistoryAPIView.as_view(), name="low-stock-history"),
    path("low-stock/export/", LowStockExportAPIView.as_view(), name="low-stock-export"),

]
