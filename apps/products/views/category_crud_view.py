from django.db.models import ProtectedError
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.paginations import StandardPagination
from apps.products.models import Category
from apps.products.serializers.category_crud_serializer import (
    CategorySerializer,
    CategoryListSerializer,
    CategoryDetailSerializer,
)


from rest_framework import generics, permissions
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes


# ─────────────────────────────────────────────
# VIEW
# ─────────────────────────────────────────────

@extend_schema(
    tags=["Category"],
    summary="Kategoriyalar ro'yxati (search, ordering, pagination bilan)",
    parameters=[
        OpenApiParameter(
            "search", OpenApiTypes.STR,
            description="Nom yoki tavsif bo'yicha qidirish (?search=elektronika)",
        ),
        OpenApiParameter(
            "ordering", OpenApiTypes.STR,
            description="Tartiblash: name, -name, created_at, -created_at",
        ),
        OpenApiParameter(
            "page", OpenApiTypes.INT,
            description="Sahifa raqami",
        ),
        OpenApiParameter(
            "limit", OpenApiTypes.INT,
            description="Sahifadagi yozuvlar soni (max 100)",
        ),
    ],
)
class CategoryListAPIView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CategoryListSerializer
    pagination_class = StandardPagination

    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]  # default tartiblash

    def get_queryset(self):
        # ✅ YAXSHI: ListAPIView + pagination/search/ordering ishlatilgan.
        # ⚠️ MUAMMO [PERFORMANCE]: `.all()` reference table uchun odatda xavfsiz, ammo `only()` ishlatilmagan.
        # Sabab: list serializerga kerakli maydonlar aniq bo'lsa ham modelning barcha ustunlari olinadi.
        # Natija: image/description kabi og'ir maydonlar bo'lsa ortiqcha I/O chiqadi.
        # ✅ YECHIM:
        # return Category.objects.only("id", "slug", "name", "description", "image", "created_at").order_by("name")
        # .all() o'rniga aniq queryset — kelajakda filter qo'shish osonroq.
        # Masalan: is_active filtri kerak bo'lsa shu yerga .filter(is_active=True) qo'shiladi.
        return Category.objects.all()


# @extend_schema(
#         tags=["Category"],
#         summary="- Kategoriyalar ro'yxati.",
#     )
# class CategoryListAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = CategoryListSerializer
#
#     def get(self, request):
#         qs = Category.objects.all()
#         serializer = self.serializer_class(qs, many=True, context={"request": request})
#         return Response(serializer.data, status=200)



@extend_schema(
    tags=["Category"],
    summary="- Kategoriya yaratish.",
)
class CategoryCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CategorySerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid(raise_exception=True):
            obj = serializer.save()
            return Response(self.serializer_class(obj).data, status=201)
        return  Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class CategoryDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CategoryDetailSerializer

    @extend_schema(
        tags=["Category"],
        summary="- ID orqali bitta Kategoriya malumotini olish",
    )
    def get(self, request, pk):
        category = get_object_or_404(Category, pk=pk)
        serializer = self.serializer_class(category)
        return Response(serializer.data, status=200)

    @extend_schema(
        tags=["Category"],
        summary="- Kategoriya tahrirlash.",
    )
    def put(self, request, pk):
        category = get_object_or_404(Category, pk=pk)
        serializer = self.serializer_class(instance=category, data=request.data, partial=True)
        if serializer.is_valid(raise_exception=True):
            obj = serializer.save()
            # ⚠️ MUAMMO [CLEAN CODE]: Update muvaffaqiyatida `201 Created` qaytmoqda.
            # Sabab: PUT mavjud resursni yangilaydi, yangi resurs yaratmaydi.
            # Natija: API clientlar status kodni noto'g'ri talqin qilishi mumkin.
            # ✅ YECHIM:
            # return Response(self.serializer_class(obj).data, status=status.HTTP_200_OK)
            return Response(self.serializer_class(obj).data, status=201)
        return  Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        tags=["Category"],
        summary="- Kategoriyani o'chirish.",
    )
    def delete(self, request, pk):
        category = get_object_or_404(Category, pk=pk)
        try:
            category.delete()
        except ProtectedError:
            raise ValidationError({
                "detail": "Bu categoryga bog‘langan productlar mavjud. Avval ularni o‘chiring yoki productni boshqa categoryga biriktiring!"
            })

        return Response(status=status.HTTP_204_NO_CONTENT)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [PUT response statusini 200 OK ga almashtirish]
# ═══════════════════════════════
