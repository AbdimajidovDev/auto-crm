from django.urls import path
from apps.transfer.views import (
    TransferListAPIView,
    TransferCreateAPIView,
    TransferApproveAPIView,
    TransferRejectAPIView, NotificationListAPIView
)


urlpatterns = [
    path('', TransferListAPIView.as_view()),
    path('create/', TransferCreateAPIView.as_view()),
    path('<int:pk>/approve/', TransferApproveAPIView.as_view()),
    path('<int:pk>/reject/', TransferRejectAPIView.as_view()),

    path('notifications/', NotificationListAPIView.as_view()),
]