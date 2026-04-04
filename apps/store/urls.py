from django.urls import path
from .views import StoreCreateAPIView, StoreListAPIView
from .views.store_user_view import StoreUserAttachAPIView

urlpatterns = [
    path('', StoreListAPIView.as_view()),
    path('create/', StoreCreateAPIView.as_view()),

    path('user-attach/', StoreUserAttachAPIView.as_view()),
]