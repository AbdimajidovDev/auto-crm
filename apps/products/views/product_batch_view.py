from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Case, When, Value, IntegerField
from django.core.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.models.timestamp_mixin import TimestampMixin
from apps.products.models import ProductBatch, ProductUnitMeasurement, ProductLocation
from apps.store.models import StoreUser

from apps.products.serializers import ProductBatchSearchSerializer, ProductUnitMeasurementSerializer, \
    ProductLocationSerializer, ProductUnitMeasurementGetSerializer, ProductLocationGetSerializer


@extend_schema(
    tags=["Product"],
    summary="Product name orqali qidirish.",
)
class ProductSearchAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, product_name):
        user = request.user
        query = product_name

        # 🔒 BASE QUERY
        qs = ProductBatch.objects.select_related(
            "product",
            "product__category",
            "store"
        ).filter(
            is_active=True,
            product__is_active=True
        )

        # 🔐 ACCESS CONTROL (MUHIM)
        if not user.is_superuser:
            store_ids = list(
                StoreUser.objects.filter(
                    user=user,
                    is_active=True
                ).values_list("store_id", flat=True)
            )

            if not store_ids:
                raise PermissionDenied("User store bilan bog‘lanmagan")

            qs = qs.filter(store_id__in=store_ids)

        # 🔍 SEARCH + RANKING
        if query:
            qs = qs.filter(
                Q(product__name__icontains=query)
            ).annotate(
                priority=Case(
                    When(product__name__iexact=query, then=Value(3)),
                    When(product__name__istartswith=query, then=Value(2)),
                    When(product__name__icontains=query, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField()
                )
            ).order_by("-priority", "-created_at")
        else:
            qs = qs.order_by("-created_at")

        # ❗ HARD LIMIT (pagination o‘rniga himoya)
        qs = qs[:100]

        serializer = ProductBatchSearchSerializer(qs, many=True)
        return Response(serializer.data)


class ProductUnitMeasurementView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProductUnitMeasurementSerializer

    @extend_schema(
        tags=["Product"],
        summary="Product o'lchov birliklari ro'yxati.",
    )
    def get(self, request):
        measurements = ProductUnitMeasurement.objects.all()
        serializer = ProductUnitMeasurementGetSerializer(measurements, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Product"],
        summary="Product o'lchov birliklarini yaratish.",
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ProductLocationView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProductLocationSerializer

    @extend_schema(
        tags=["Product"],
        summary="Productni do'kondagi joylashuvlar ro'yxati.",
    )
    def get(self, request):
        locations = ProductLocation.objects.all()
        serializer = ProductLocationGetSerializer(locations, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Product"],
        summary="Productni do'kondagi joylashuvini yaratish.",
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)