from django.urls import path
from apps.transfer.views import (
    TransferListAPIView,
    TransferCreateAPIView,
    TransferApproveAPIView,
    TransferRejectAPIView, NotificationListAPIView,
    TransferSessionListCreateAPIView,
    TransferSessionDetailAPIView,
    TransferSessionCompleteAPIView,
)
from apps.transfer.export_views import TransferExportAPIView


urlpatterns = [
    path('', TransferListAPIView.as_view()),
    path('export/', TransferExportAPIView.as_view()),
    path('create/', TransferCreateAPIView.as_view()),

    # O'tkazma qoralamasi (sessiya): avto-saqlash + davom ettirish
    path('session/', TransferSessionListCreateAPIView.as_view()),
    path('session/<int:pk>/', TransferSessionDetailAPIView.as_view()),
    path('session/<int:pk>/complete/', TransferSessionCompleteAPIView.as_view()),

    path('<int:pk>/approve/', TransferApproveAPIView.as_view()),
    path('<int:pk>/reject/', TransferRejectAPIView.as_view()),

    path('notifications/', NotificationListAPIView.as_view()),
]