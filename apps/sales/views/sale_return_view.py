from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, generics
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.sales.models import SaleReturn, SaleReturnItem
from apps.sales.serializers import SaleReturnCreateSerializer, SaleReturnListSerializer
from apps.sales.services.sale_return_service import SaleReturnService


@extend_schema(
    tags=['Sales'],
    summary="Sotuvni qaytarish."
)
class SaleReturnListAPIView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SaleReturnListSerializer

    def get_queryset(self):
        user = self.request.user
        qs = (
            SaleReturn.objects
            .select_related("store", "seller", "customer", "sale")
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=SaleReturnItem.objects.select_related("product")
                )
            )
            .order_by("-created_at")
        )

        # Davr/do'kon filtri — sotuv statistikasi (SaleStatisticsAPIView) bilan
        # bir xil parametrlar: ikkala sahifa raqamlari solishtirma bo'ladi
        params = self.request.query_params
        store = params.get("store")
        if store:
            qs = qs.filter(store_id=store)
        date_from = params.get("date_from")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = params.get("date_to")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        if user.is_superuser:
            return qs

        return qs.filter(
            store__user_links__user=user,
            store__user_links__is_active=True,
        )


#
# @extend_schema(
#     tags=['Sales'],
#     summary="Sotuvni qaytarish."
# )
# class SaleReturnListAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = SaleReturnListSerializer
#
#     def get(self, request):
#         # ⚠️ MUAMMO [KRITIK/PERFORMANCE]: Filtrsiz `.all()` va prefetch/select_related yo'q.
#         # Sabab: serializer `store`, `seller`, `items`, `items__product` maydonlariga murojaat qiladi.
#         # Natija: N+1 query va katta jadvalda pagination/filter yo'qligi sabab endpoint sekinlashadi.
#         # ✅ YECHIM:
#         # sale_return = (
#         #     SaleReturn.objects
#         #     .select_related("store", "seller", "customer", "sale")
#         #     .prefetch_related(Prefetch("items", queryset=SaleReturnItem.objects.select_related("product")))
#         #     .order_by("-created_at")
#         # )
#         # N+1: list serializer `store`, `seller`, `items`, `items__product` ga tegadi — `select_related`
#         # va `prefetch_related` (masalan `Prefetch("items", queryset=...select_related("product"))`)
#         # qo'shmasa har bir qator uchun alohida so'rovlar ko'payadi.
#         sale_return = SaleReturn.objects.all()
#         serializer = self.serializer_class(sale_return, many=True)
#         return Response(serializer.data, status=200)


@extend_schema(
    tags=['Sales'],
    summary="Sotuvni qaytarish."
)
class SaleReturnCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SaleReturnCreateSerializer

    @extend_schema(
        tags=['Sales'],
        summary="Sotuvni qaytarish",
    )
    def post(self, request):

        serializer = SaleReturnCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return_obj = SaleReturnService.create_return(
            user=request.user,
            data=serializer.validated_data
        )

        return Response({
            "return_id": return_obj.id,
            "refund": return_obj.total_refund
        }, status=201)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [SaleReturnListAPIView querysetini select_related/prefetch_related va pagination bilan optimallashtirish]
# ═══════════════════════════════
