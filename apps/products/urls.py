from django.urls import path

from apps.products.views.category_crud_view import (
    CategoryCreateAPIView,
    CategoryListAPIView,
    CategoryDetailAPIView,
)
from apps.products.views.product_crud_view import (
    ProductCreateAPIView,
    ProductListAPIView,
    BatchByBarcodeAPIView, ProductDetailAPIView,
)


urlpatterns = [

    # Category
    path("categories/", CategoryListAPIView.as_view()),
    path("categories/create/", CategoryCreateAPIView.as_view()),
    path("categories/<int:pk>/", CategoryDetailAPIView.as_view()),

    # Product
    path("", ProductListAPIView.as_view()),
    path("create/", ProductCreateAPIView.as_view()),
    path("<int:pk>/", ProductDetailAPIView.as_view()),

    path("barcode/<str:barcode>/", BatchByBarcodeAPIView.as_view()),
]
