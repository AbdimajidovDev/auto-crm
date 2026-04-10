from django.urls import path
from apps.sales.views import (
    SaleCreateAPIView,
    SaleListAPIView,
    SaleDetailAPIView,
)

urlpatterns = [
    path('list/', SaleListAPIView.as_view()),
    path('create/', SaleCreateAPIView.as_view()),
    path('<int:pk>/', SaleDetailAPIView.as_view()),
]