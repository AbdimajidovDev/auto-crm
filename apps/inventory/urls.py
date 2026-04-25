from django.urls import path
from .views import *
from .views.inventory_view import (
    InventoryListAPIView,
    InventoryStartAPIView,
    InventorySetCountAPIView,
    InventoryFinalizeAPIView,
    InventoryCancelAPIView, InventoryMovementListView,
)



urlpatterns = [
    path('list/<int:session_id>/', InventoryListAPIView.as_view()),
    path('movement-list/<int:session_id>/', InventoryMovementListView.as_view()),

    path('start/', InventoryStartAPIView.as_view()),
    path('scan/', InventorySetCountAPIView.as_view()),

    path('finalize/', InventoryFinalizeAPIView.as_view()),
    path('cancel/', InventoryCancelAPIView.as_view()),
]
