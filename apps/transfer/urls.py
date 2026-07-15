from django.urls import path
from apps.transfer.views import (
    TransferListAPIView,
    TransferCreateAPIView,
    TransferApproveAPIView,
    TransferRejectAPIView, NotificationListAPIView
)
from apps.transfer.export_views import TransferExportAPIView


urlpatterns = [
    path('', TransferListAPIView.as_view()),
    path('export/', TransferExportAPIView.as_view()),
    path('create/', TransferCreateAPIView.as_view()),
    path('<int:pk>/approve/', TransferApproveAPIView.as_view()),
    path('<int:pk>/reject/', TransferRejectAPIView.as_view()),

    path('notifications/', NotificationListAPIView.as_view()),
]