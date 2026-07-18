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

class CategoryPagination(StandardPagination):
    """Kategoriyalar uchun pagination: default 100, maksimum 100 (?limit=1000 ham 100 ga qisqaradi)."""

    page_size = 100
    max_page_size = 100


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
            description="Sahifadagi yozuvlar soni (default 100, max 100)",
        ),
    ],
)
class CategoryListAPIView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CategoryListSerializer
    pagination_class = CategoryPagination

    filter_backends = [SearchFilter, OrderingFilter]
    # Qidiruv tarjima ustunlarini ham qamrab oladi (lotin/kirill nomlar)
    search_fields = ["name", "name_uz", "name_uz_cyrl", "description"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]  # default tartiblash

    def get_queryset(self):
        # only(): serializer ishlatadigan ustunlargina o'qiladi.
        # Diqqat: modeltranslation `name`/`description` ni faol tildagi ustundan
        # o'qiydi, shuning uchun tarjima ustunlari ham ro'yxatda bo'lishi shart —
        # aks holda har bir qator uchun deferred-load (qo'shimcha SQL) chiqadi.
        return Category.objects.only(
            "id", "slug", "image", "created_at",
            "name", "name_uz", "name_uz_cyrl",
            "description", "description_uz", "description_uz_cyrl",
        )


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
