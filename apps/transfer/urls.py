from django.urls import path

from apps.transfer.views import StockTransferAPIView



urlpatterns = [
    path('create/', StockTransferAPIView.as_view()),
]
