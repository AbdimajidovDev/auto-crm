from django.db.models import ProtectedError
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Category
from apps.products.serializers.category_crud_serializer import (
    CategorySerializer,
    CategoryListSerializer,
    CategoryDetailSerializer,
)


@extend_schema(
        tags=["Category"],
        summary="- Kategoriyalar ro'yxati.",
    )
class CategoryListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CategoryListSerializer

    def get(self, request):
        qs = Category.objects.all()
        return Response(self.serializer_class(qs, many=True).data)



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

