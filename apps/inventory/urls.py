from django.urls import path

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
    path('list/<int:session_id>/', InventoryDetailAPIView.as_view()),
    path('movement-list/<int:session_id>/', InventoryMovementListView.as_view()),

    path('start/', InventoryStartAPIView.as_view()),
    path('scan/', InventorySetCountAPIView.as_view()),

    path('finalize/', InventoryFinalizeAPIView.as_view()),
    path('cancel/', InventoryCancelAPIView.as_view()),

    path("sessions/<int:session_id>/over/", InventoryOverCountView.as_view(), name="inventory-over-count"),
    path("sessions/<int:session_id>/short/", InventoryShortCountView.as_view(), name="inventory-short-count"),

]
