from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Category
from apps.products.serializers.category_crud_serializer import (
    CategorySerializer,
    CategoryListSerializer,
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
