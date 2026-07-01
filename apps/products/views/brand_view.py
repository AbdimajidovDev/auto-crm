from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.serializers.brand_serializer import BrandSerializer
from apps.products.services.brand_service import (
    BrandService,
)


class BrandListCreateAPIView(APIView):
    # ⚠️ MUAMMO [ARXITEKTURA/xavfsizlik]: `permission_classes` belgilanmagan — global DEFAULT'ga tayanadi.
    # Agar loyiha default'i AllowAny bo'lsa, brend ro'yxati/yaratish autentifikatsiyasiz ochiladi.
    # ✅ YECHIM: aniq belgilash: `permission_classes = [permissions.IsAuthenticated]`.
    serializer_class = BrandSerializer

    def get(self, request):
        # ⚠️ MUAMMO [PERF]: `BrandService.list()` → `Brand.objects.all()` paginationsiz `many=True` bilan qaytadi.
        # Brend katalogi odatda kichik, shu sabab prioritet PAST, lekin katalog o'ssa butun jadval bir javobda.
        # ✅ YECHIM: ListAPIView + StandardPagination ga o'tkazish yoki qidiruv/limit qo'shish:
        #   class BrandListCreateAPIView(generics.ListCreateAPIView):
        #       queryset = Brand.objects.all().order_by("name")
        #       pagination_class = StandardPagination
        brands = BrandService.list()
        serializer = BrandSerializer(brands, many=True, context={"request": request})

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


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1  (list paginationsiz — katalog kichik bo'lgani uchun past prioritet)
# Arxitektura muammolari: 1  (permission_classes belgilanmagan)
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [permission_classes'ni aniq belgilash; keyin pagination]
# ═══════════════════════════════
