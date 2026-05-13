from rest_framework import status, permissions, generics
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Case, When, Value, IntegerField
from django.core.exceptions import PermissionDenied
from rest_framework.views import APIView

from apps.common.paginations import StandardPagination
from apps.products.models import ProductBatch, ProductUnitMeasurement, ProductLocation
from apps.store.models import StoreUser

from apps.products.serializers import (
    ProductBatchSearchSerializer,
    ProductUnitMeasurementSerializer,
    ProductLocationSerializer,
    ProductUnitMeasurementGetSerializer,
    ProductLocationGetSerializer,
    ProductBatchLocationUpdateSerializer,
)

from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from drf_spectacular.types import OpenApiTypes



@extend_schema(
    tags=["Product"],
    summary="Batch joylashuvini yangilash (Faqat location).",
    request=ProductBatchLocationUpdateSerializer,  # Swagger-da faqat location ko'rinishi uchun
)
class ProductBatchDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def put(self, request, pk):
        batch = get_object_or_404(ProductBatch, pk=pk)

        serializer = ProductBatchLocationUpdateSerializer(batch, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Product"],
    summary="Product name orqali qidirish.",
)
class ProductSearchAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, product_name):
        user = request.user
        query = product_name

        # ✅ YAXSHI: Qidiruv querysetida product/category/store `select_related` qilingan.
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
        # Eslatma: querysetni `[:100]` bilan kesish keyin pagination/filter bilan aralashsa
        # noaniq natija beradi; lekin bu yerda maqsadli himoya.
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
        # ⚠️ MUAMMO [PERFORMANCE]: Filtrsiz `.all()` va cache yo'q.
        # Sabab: o'lchov birliklari kam o'zgaradigan reference data, har requestda DBdan olinadi.
        # Natija: tez-tez chaqiriladigan endpointlarda keraksiz DB yuklama paydo bo'ladi.
        # ✅ YECHIM:
        # measurements = cache.get_or_set(
        #     "product_unit_measurements",
        #     ProductUnitMeasurement.objects.only("id", "measurement").order_by("measurement"),
        #     3600,
        # )
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


class ProductUnitMeasurementDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = ProductUnitMeasurementSerializer

    @extend_schema(
        tags=["Product"],
        summary="ID orqali Product o'lchov birlik malumotini olish!",
    )
    def get(self, request, pk):
        measurement = get_object_or_404(ProductUnitMeasurement, pk=pk)
        serializer = ProductUnitMeasurementGetSerializer(measurement, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Product"],
        summary="Product o'lchov birligini tahrirlash.",
    )
    def put(self, request, pk):
        measurement = get_object_or_404(ProductUnitMeasurement, pk=pk)
        serializer = self.serializer_class(instance=measurement, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        tags=["Product"],
        summary="Productni do'kondagi joylashuvini o'chirish.",
    )
    def delete(self, request, pk):
        measurement = get_object_or_404(ProductUnitMeasurement, pk=pk)
        measurement.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────
# VIEW
# ─────────────────────────────────────────────

@extend_schema_view(
    get=extend_schema(
        tags=["Product"],
        summary="Mahsulot joylashuvlar ro'yxati",
        parameters=[
            OpenApiParameter(
                "search", OpenApiTypes.STR,
                description="Joylashuv nomi bo'yicha qidirish (?search=sklad)",
            ),
            OpenApiParameter(
                "ordering", OpenApiTypes.STR,
                description="Tartiblash: location, -location, created_at, -created_at",
            ),
            OpenApiParameter("page", OpenApiTypes.INT, description="Sahifa raqami"),
            OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni"),
        ],
    ),
    post=extend_schema(
        tags=["Product"],
        summary="Yangi mahsulot joylashuvi yaratish",
    ),
)
class ProductLocationView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination

    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["location", "description"]
    ordering_fields = ["location", "created_at"]
    ordering = ["location"]

    def get_queryset(self):
        # ⚠️ MUAMMO [PERFORMANCE]: Filtrsiz `.all()` ishlatilgan.
        # Sabab: location reference data bo'lsa ham `only()`/cache yo'q; jadval kattalashsa barcha ustunlar o'qiladi.
        # Natija: list endpoint ortiqcha ustun va satrlarni olib keladi.
        # ✅ YECHIM:
        # return ProductLocation.objects.only("id", "location", "description", "created_at").order_by("location")
        return ProductLocation.objects.all()

    def get_serializer_class(self):
        # GET → ro'yxat uchun (created_at ham ko'rinadi)
        # POST → yaratish uchun (faqat yozish kerak bo'lgan maydonlar)
        if self.request.method == "POST":
            return ProductLocationSerializer
        return ProductLocationGetSerializer


# class ProductLocationView(APIView):
#     permission_classes = [IsAuthenticated]
#     serializer_class = ProductLocationSerializer
#
#     @extend_schema(
#         tags=["Product"],
#         summary="Productni do'kondagi joylashuvlar ro'yxati.",
#     )
#     def get(self, request):
#         locations = ProductLocation.objects.all()
#         serializer = ProductLocationGetSerializer(locations, many=True, context={'request': request})
#         return Response(serializer.data, status=status.HTTP_200_OK)
#
#     @extend_schema(
#         tags=["Product"],
#         summary="Productni do'kondagi joylashuvini yaratish.",
#     )
#     def post(self, request):
#         serializer = self.serializer_class(data=request.data)
#         if serializer.is_valid():
#             serializer.save()
#             return Response(serializer.data, status=status.HTTP_201_CREATED)
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ProductLocationDetailView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProductLocationSerializer

    @extend_schema(
        tags=["Product"],
        summary="ID orqali Productni do'kondagi joylashuv malumotini olish! ",
    )
    def get(self, request, pk):
        location = get_object_or_404(ProductLocation, pk=pk)
        serializer = self.serializer_class(location, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Product"],
        summary="Productni do'kondagi joylashuvini tahrirlash.",
    )
    def put(self, request, pk):
        location = get_object_or_404(ProductLocation, pk=pk)
        serializer = self.serializer_class(instance=location, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        tags=["Product"],
        summary="Productni do'kondagi joylashuvini o'chirish.",
    )
    def delete(self, request, pk):
        location = get_object_or_404(ProductLocation, pk=pk)
        # ⚠️ MUAMMO [KRITIK]: Delete uchun `ProtectedError` yoki ishlatilayotgan location tekshiruvi yo'q.
        # Sabab: `ProductBatch` shu locationga bog'langan bo'lsa DB exception responsega nazoratsiz chiqishi mumkin.
        # Natija: 500 xato yoki ma'lumot yaxlitligi buzilishi xavfi paydo bo'ladi.
        # ✅ YECHIM:
        # if ProductBatch.objects.filter(location=location).exists():
        #     raise ValidationError("Bu location ishlatilgan, o'chirib bo'lmaydi")
        location.delete()
        return Response('Location successfully deleted!', status=status.HTTP_204_NO_CONTENT)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 2
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Reference endpointlarga cache/only qo'shish va location delete himoyasini kiritish]
# ═══════════════════════════════
