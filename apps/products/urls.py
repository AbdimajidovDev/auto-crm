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
from apps.products.views.product_excel_view import (
    ProductImportAPIView,
    ProductImportTemplateAPIView,
    ProductExcelLookupAPIView,
)
from apps.products.views.export_views import ProductExportAPIView, CategoryExportAPIView
from apps.products.views.product_bulk_view import ProductBulkStatusAPIView, ProductBulkDeleteAPIView

urlpatterns = [

    # Category
    path("categories/", CategoryListAPIView.as_view()),
    path("categories/export/", CategoryExportAPIView.as_view()),
    path("categories/create/", CategoryCreateAPIView.as_view()),
    path("categories/<int:pk>/", CategoryDetailAPIView.as_view()),

    # Product
    path("", ProductListAPIView.as_view()),
    path("export/", ProductExportAPIView.as_view()),
    path("create/", ProductCreateAPIView.as_view()),
    path("bulk-status/", ProductBulkStatusAPIView.as_view()),
    path("bulk-delete/", ProductBulkDeleteAPIView.as_view()),
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
    path("brand/", BrandListCreateAPIView.as_view(),),
    path("brand/<int:pk>/", BrandRetrieveUpdateDestroyAPIView.as_view(),),

    # Excel Import, Export
    path("products/import/",          ProductImportAPIView.as_view()),
    path("products/import/template/", ProductImportTemplateAPIView.as_view()),
    # Sotuv cheki uchun: Excel'dan mahsulotlarni topish (import emas — faqat o'qish)
    path("products/import/lookup/",   ProductExcelLookupAPIView.as_view()),

]
