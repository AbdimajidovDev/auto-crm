from django.urls import path

from apps.products.views.category_crud_view import (
    CategoryCreateAPIView,
    CategoryListAPIView,
)
from apps.products.views.product_crud_view import (
    ProductCreateAPIView,
    ProductByBarcodeAPIView,
    ProductListAPIView,
)


urlpatterns = [

    # Category
    path("categories/", CategoryListAPIView.as_view()),
    path("categories/create/", CategoryCreateAPIView.as_view()),

    # Product
    path("", ProductListAPIView.as_view()),
    path("create/", ProductCreateAPIView.as_view()),
    path("barcode/<str:barcode>/", ProductByBarcodeAPIView.as_view()),
]