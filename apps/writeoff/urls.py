from django.urls import path

from apps.writeoff.views import (
    WriteOffListAPIView,
    WriteOffCreateAPIView,
    WriteOffDetailAPIView,
)

urlpatterns = [
    path("list/", WriteOffListAPIView.as_view()),
    path("create/", WriteOffCreateAPIView.as_view()),
    path("<int:pk>/", WriteOffDetailAPIView.as_view()),
]
