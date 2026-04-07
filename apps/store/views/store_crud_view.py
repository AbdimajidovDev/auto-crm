from drf_spectacular.utils import extend_schema
from rest_framework.generics import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from django.core.exceptions import ValidationError

from apps.store.models import Store
from apps.store.selectors import StoreSelector
from apps.store.services import StoreService
from apps.store.serializers import (
    StoreCreateSerializer,
    StoreResponseSerializer,
    StoreListSerializer,
    StoreDetailSerializer
)


@extend_schema(
    tags=['Store'],
    summary="- Barcha do'konlarni ko'rish uchun API.",
)
class StoreListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_classes = StoreListSerializer

    def get(self, request):
        shops = StoreSelector.store_list()
        serializer = self.serializer_classes(shops, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['Store'],
    summary="- Do'kon yaratish uchun API.",
)
class StoreCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StoreCreateSerializer

    def post(self, request):

        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid(raise_exception=True):
            store = StoreService.create_store(
                user=request.user,
                data=serializer.validated_data
            )

            return Response(
                StoreResponseSerializer(store).data,
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class StoreDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StoreDetailSerializer

    @extend_schema(
        tags=['Store'],
        summary="- ID orqali bitta Do'kon malumotini olish",
    )
    def get(self, request, pk):
        store = StoreSelector.get_store(pk)
        serializer = self.serializer_class(store)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=['Store'],
        summary="- Do'kon malumotlarini tahrirlash",
    )
    def put(self, request, pk):
        store = get_object_or_404(Store, pk=pk)
        serializer = self.serializer_class(store, data=request.data, partial=True)
        if serializer.is_valid(raise_exception=True):
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        tags=['Store'],
        summary="- Do'kon o'chirish",
    )
    def delete(self, request, pk):
        store = StoreSelector.get_store(pk)
        store.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)