from django.urls import path
from .views import *
from .views.inventory_view import (
    InventoryListAPIView,
    InventoryStartAPIView,
    InventorySetCountAPIView,
    InventoryFinalizeAPIView,
    InventoryCancelAPIView,
)



urlpatterns = [
    path('inventory/list/<int:session_id>/', InventoryListAPIView.as_view()),
    path('inventory/start/', InventoryStartAPIView.as_view()),
    path('inventory/scan/', InventorySetCountAPIView.as_view()),
    path('inventory/finalize/', InventoryFinalizeAPIView.as_view()),
    path('inventory/cancel/', InventoryCancelAPIView.as_view()),
]
