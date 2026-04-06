from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from django.core.exceptions import ValidationError

from apps.store.selectors import StoreSelector
from apps.store.services import StoreService
from apps.store.serializers import (
    StoreCreateSerializer,
    StoreResponseSerializer, StoreListSerializer
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
        serializer = self.serializer_classes(shops, many=True)
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