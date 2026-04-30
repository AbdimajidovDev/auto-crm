from django.db.models import ProtectedError
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Product, ProductBatch
from apps.products.serializers import ProductBatchListSerializer
from apps.products.serializers.product_crud_serializer import (
    ProductCreateSerializer,
    ProductGetSerializer,
    ProductListSerializer,
    ProductBatchSerializer,
    ProductUpdateSerializer,
)
from apps.products.services.product_crud_service import ProductService

from django.db.models import Prefetch

from apps.store.models import Store


# @extend_schema(
#     tags=["Product"],
#     summary="- Productlar ro'yxati.",
# )
# class ProductListAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = ProductListSerializer
#
#     def get(self, request):
#         store_id = request.query_params.get("store")
#
#         if not store_id:
#             return Response({"error": "store param required"}, status=400)
#
#         queryset = Product.objects.filter(
#             is_active=True,
#             batches__store_id=store_id,
#             batches__is_active=True
#         ).annotate(
#             total_quantity=Sum("batches__quantity")
#         ).filter(
#             total_quantity__gt=0
#         ).distinct()
#
        # # 🔎 optional filters
        # search = request.query_params.get("search")
        # category = request.query_params.get("category")
        #
        # if search:
        #     queryset = queryset.filter(name__icontains=search)
        #
        # if category:
        #     queryset = queryset.filter(category_id=category)
        #
        # serializer = self.serializer_class(
        #     queryset,
        #     many=True,
        #     context={"request": request}
        # )
        # return Response(serializer.data, status=200)


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
    serializer_class = ProductUpdateSerializer

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
        summary="- Product va uning rasmlarini tahrirlash",
    )
    def put(self, request, pk):
        product = get_object_or_404(Product, pk=pk)

        serializer = ProductUpdateSerializer(
            product,
            data=request.data,
            partial=True
        )

        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response('Product successfully updated!', status=200)


    @extend_schema(
        tags=["Product"],
        summary="- Productni o'chirish.",
    )
    def delete(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        try:
            product.delete()
        except ProtectedError:
            raise ValidationError(
                "Bu mahsulotni o‘chirib bo‘lmaydi, chunki u allaqachon tizimda ishlatilgan (kirim/sotuv mavjud)."
            )

        return Response(status=204)


        # if product.is_active:
        #     product.is_active = False
        #     product.save()
        # return Response('Product not found', status=400)



@extend_schema(
    tags=["Product"],
    summary="- barcode orqali productni olish.",
)
class BatchByBarcodeAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, barcode):
        batch = get_object_or_404(ProductBatch, barcode=barcode)
        serializer = ProductBatchSerializer(batch, context={"request": request})
        return Response(serializer.data, status=200)

#
# class ProductBatchListAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = ProductBatchSerializer
#
#     def get(self, request):
#         products = ProductBatch.objects.filter(is_active=True)
#         serializer = self.serializer_class(products, many=True, context={"request": request})
#         return Response(serializer.data, status=200)



class ProductBatchListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProductBatchListSerializer

    def get_store(self, request):
        user = request.user
        store_id = request.query_params.get("store")

        # 🔥 SUPERUSER LOGIC
        if user.is_superuser:
            if store_id:
                return Store.objects.get(id=store_id)

            # default → SKLAD
            return Store.objects.get(type='b')

        # 🔒 SELLER LOGIC
        return user.store  # ⚠️ shu joyni aniqlab ol

    @extend_schema(
        tags=["Product"],
        summary="- Sotuv paneli uchun Mahsulotlar ro'yxati.",
    )
    def get(self, request):
        selected_store = self.get_store(request)

        search = request.query_params.get("search")

        products = Product.objects.filter(is_active=True)

        if search:
            products = products.filter(name__icontains=search)

        products = products.prefetch_related(
            Prefetch(
                "batches",
                queryset=ProductBatch.objects.filter(is_active=True).select_related("store"),
                to_attr="all_batches"
            )
        )

        serializer = self.serializer_class(
            products,
            many=True,
            context={"selected_store": selected_store}
        )

        return Response(serializer.data)
