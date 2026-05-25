from django.urls import path

from apps.products.views.brand_view import BrandRetrieveUpdateDestroyAPIView, BrandListCreateAPIView
from apps.products.views.category_crud_view import (
    CategoryCreateAPIView,
    CategoryListAPIView,
    CategoryDetailAPIView,
)
from apps.products.views.product_crud_view import (
    ProductCreateAPIView,
    ProductListAPIView,
    ProductDetailAPIView,
    ProductBatchListAPIView,
)
from apps.products.views.product_batch_view import (
    ProductSearchAPIView,
    ProductLocationView,
    ProductUnitMeasurementView,
    ProductUnitMeasurementDetailView,
    ProductLocationDetailView,
    ProductBatchDetailView,
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

    path("item/list/", ProductBatchListAPIView.as_view()),
    path("item/<int:pk>/", ProductBatchDetailView.as_view()),

    # path("barcode/<str:barcode>/", BatchByBarcodeAPIView.as_view()),
    path("search/<str:product_name>/", ProductSearchAPIView.as_view()),

    path('store-product/locations/', ProductLocationView.as_view()),
    path('store-product/locations/<int:pk>/', ProductLocationDetailView.as_view()),
    path('measurements/', ProductUnitMeasurementView.as_view()),
    path('measurements/<int:pk>/', ProductUnitMeasurementDetailView.as_view()),

    # Brand
    path("", BrandListCreateAPIView.as_view(),),
    path("<int:pk>/", BrandRetrieveUpdateDestroyAPIView.as_view(),),
]
