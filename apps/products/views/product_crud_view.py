from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Product
from apps.products.serializers.product_crud_serializer import (
    ProductCreateSerializer,
    ProductGetSerializer, ProductListSerializer,
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
        products = Product.objects.all()
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


@extend_schema(
    tags=["Product"],
    summary="- Product yaratish.",
)
class ProductByBarcodeAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductGetSerializer

    def get(self, request, barcode):
        product = get_object_or_404(Product, barcode=barcode)
        serializer = self.serializer_class(product, context={"request": request})
        return Response(serializer.data, status=200)
