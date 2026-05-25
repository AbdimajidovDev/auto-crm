from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.serializers.brand_serializer import BrandSerializer
from apps.products.services.brand_service import (
    BrandService,
)


class BrandListCreateAPIView(APIView):

    def get(self, request):
        brands = BrandService.list()
        serializer = BrandSerializer(brands, many=True,)

        return Response(
            serializer.data
        )

    def post(self, request):
        serializer = BrandSerializer(data=request.data)
        if serializer.is_valid(raise_exception=True):
            brand = BrandService.create(validated_data=serializer.validated_data)
            return Response(BrandSerializer(brand).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BrandRetrieveUpdateDestroyAPIView(APIView):

    def get(self, request, pk):
        brand = BrandService.get(pk)
        serializer = BrandSerializer(brand)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        brand = BrandService.get(pk)
        serializer = BrandSerializer(brand, data=request.data, )
        serializer.is_valid(raise_exception=True)
        brand = BrandService.update(
            instance=brand,
            validated_data=serializer.validated_data,
        )

        return Response(BrandSerializer(brand).data)

    def delete(self, request, pk):
        BrandService.delete(brand_id=pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
