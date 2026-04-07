from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Product, ProductBatch
from apps.products.serializers.product_crud_serializer import (
    ProductCreateSerializer,
    ProductGetSerializer,
    ProductListSerializer,
    ProductImageSerializer,
)
from apps.products.services.product_crud_service import ProductService



@extend_schema(
    tags=["Product"],
    summary="- Productlar ro'yxati.",
)
class ProductListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductListSerializer

    def get(self, request):
        products = Product.objects.filter(is_active=True)
        serializer = self.serializer_class(products, many=True, context={"request": request})
        return Response(serializer.data, status=200)


@extend_schema(
    tags=["Product"],
    summary="- Product yaratish.",
)
class ProductCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductCreateSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            product = ProductService.create_product(serializer.validated_data)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response(ProductGetSerializer(product).data, status=201)


class ProductDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductListSerializer

    @extend_schema(
        tags=["Product"],
        summary="- ID orqali Product malumotlarini olish.",
    )
    def get(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        serializer = self.serializer_class(product)
        return Response(serializer.data, status=200)

    @extend_schema(
        tags=["Product"],
        summary="- Product tahrirlash.",
    )
    def put(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        serializer = ProductCreateSerializer(product, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=200)
        return Response(serializer.errors, status=400)

    @extend_schema(
        tags=["Product"],
        summary="- Productni o'chirish.",
    )
    def delete(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        if product.is_active:
            product.is_active = False
            product.save()
            return Response(status=204)
        return Response('Product not found', status=400)



@extend_schema(
    tags=["Product"],
    summary="- barcode orqali productni olish.",
)
class BatchByBarcodeAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, barcode):
        store_id = request.query_params.get("store_id")

        batch = get_object_or_404(
            ProductBatch,
            barcode=barcode,
            store_id=store_id
        )

        return Response({
            "product": batch.product.name_uz,
            "price": batch.selling_price,
            "quantity": batch.quantity
        })

