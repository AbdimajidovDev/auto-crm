from django.urls import path
from apps.store.views import (
    StoreCreateAPIView,
    StoreListAPIView,
    StoreDetailAPIView,
)
from apps.store.views.store_user_view import StoreUserAttachAPIView

urlpatterns = [
    path('', StoreListAPIView.as_view()),
    path('create/', StoreCreateAPIView.as_view()),
    path('<int:pk>/', StoreDetailAPIView.as_view()),

    # path('user-attach/', StoreUserAttachAPIView.as_view()),
]