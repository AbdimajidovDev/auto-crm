from drf_spectacular.utils import extend_schema
from rest_framework.generics import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from django.core.exceptions import ValidationError

from apps.common.paginations import StandardPagination
from apps.sales.models import Sale
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
        # ⚠️ MUAMMO [PERF]: Ro'yxat paginationsiz qaytadi — `store_list()` butun jadvalni (`.all()`) yuklaydi.
        # Do'konlar soni odatda kichik, lekin har do'kon uchun serializerdagi `get_sellers` alohida
        # `user_links` query yuboradi (N+1). 48k StockEntry emas, do'kon soni × sotuvchi query masshtabi.
        # ✅ YECHIM: selectorda `prefetch_related("user_links__user")` (store_crud_selector.py da flag qilingan)
        # + kerak bo'lsa StandardPagination qo'shish:
        #   class StoreListAPIView(generics.ListAPIView): pagination_class = StandardPagination ...
        shops = StoreSelector.store_list()

        # `page` param kelsa sahifalangan javob ({count, results, ...}) qaytadi —
        # do'konlar ro'yxati sahifasi shu rejimda ishlaydi. Param bo'lmasa avvalgidek
        # to'liq massiv qaytadi: select/dropdown ishlatuvchilar (masalan transfer,
        # kirim dialoglari) barcha do'konlarni kutadi, ular buzilmasligi kerak.
        if request.query_params.get("page"):
            paginator = StandardPagination()
            page = paginator.paginate_queryset(shops, request, view=self)
            serializer = self.serializer_classes(page, many=True, context={'request': request})
            return paginator.get_paginated_response(serializer.data)

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
        # ⚠️ MUAMMO [PERF]: `get_store(pk)` prefetchsiz bitta obyekt qaytaradi, `StoreDetailSerializer.get_sellers`
        # esa `user_links.filter(is_active=True).select_related("user")` bilan qo'shimcha 1 query yuboradi.
        # Bitta obyekt uchun kam xarajat, lekin barqaror query byudjeti uchun prefetch afzal.
        # ✅ YECHIM: get_object_or_404 querysetiga
        #   Prefetch("user_links", queryset=StoreUser.objects.filter(is_active=True).select_related("user"))
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
        # Sotuv/kirim tarixi bor do'konni o'chirib bo'lmaydi — Sale.store endi
        # PROTECT, aks holda bitta o'chirish do'konning butun moliyaviy
        # tarixini (Sale, SaleItem, Payment, ProductBatch) olib ketardi.
        store = StoreSelector.get_store(pk)

        if Sale.objects.filter(store=store).exists():
            return Response(
                {"detail": "Bu do'konda sotuvlar tarixi bor — o'chirib bo'lmaydi."},
                status=status.HTTP_409_CONFLICT,
            )

        store.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 2
#   - StoreListAPIView.get: paginationsiz `.all()` + serializerda sellers N+1 (store_crud_selector.py da flag)
#   - StoreDetailAPIView.get: sellers uchun prefetchsiz qo'shimcha query
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [store_list querysetiga user_links prefetch + list pagination]
# ═══════════════════════════════