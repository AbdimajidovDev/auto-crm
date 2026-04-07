from django.urls import path
from apps.transfer.views import (
    TransferCreateAPIView,
    TransferApproveAPIView,
    TransferRejectAPIView,
)

urlpatterns = [
    path("create/", TransferCreateAPIView.as_view()),
    path("<int:pk>/approve/", TransferApproveAPIView.as_view()),
    path("<int:pk>/reject/", TransferRejectAPIView.as_view()),
]