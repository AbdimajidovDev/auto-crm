from collections import defaultdict

from django.db.models import Prefetch, Sum
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, generics
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.paginations import StandardPagination
from apps.common.store_scope import scope_queryset
from apps.sales.models import Payment, SaleReturn, SaleReturnItem
from apps.sales.serializers import SaleReturnCreateSerializer, SaleReturnListSerializer
from apps.sales.services.sale_return_service import SaleReturnService


@extend_schema(
    tags=['Sales'],
    summary="Sotuvni qaytarish."
)
class SaleReturnListAPIView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SaleReturnListSerializer
    pagination_class = StandardPagination

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

        return scope_queryset(qs, user)

    def list(self, request, *args, **kwargs):
        """
        Qaytarim to'lovlarini sahifa bo'yicha bitta so'rovda yuklaydi.

        Serializer `get_refund_payments` da har qator uchun alohida
        `Payment.objects.filter(payment_group=...)` so'rovi yuborardi (N+1).
        `payment_group` FK emas (UUID maydon), shuning uchun Prefetch o'rniga
        sahifadagi guruhlar bo'yicha bitta so'rov qilib, xarita context'ga beriladi.
        """
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is None:
            page = list(queryset)
            paginated = False
        else:
            paginated = True

        # Statistika kartalari sahifalanganda ham butun (filtrlangan) davr
        # bo'yicha to'g'ri chiqishi uchun jami summa alohida hisoblanadi
        total_refund = queryset.aggregate(total=Sum("total_refund"))["total"] or 0

        groups = [r.payment_group for r in page if r.payment_group]
        payments_by_group = defaultdict(list)
        if groups:
            for payment in (
                Payment.objects
                .filter(payment_group__in=groups, is_refund=True)
                .select_related("bank_card")
                .order_by("id")
            ):
                payments_by_group[payment.payment_group].append(payment)

        serializer = self.get_serializer(
            page, many=True, context={**self.get_serializer_context(), "payments_by_group": payments_by_group}
        )
        if paginated:
            response = self.get_paginated_response(serializer.data)
            response.data["total_refund"] = str(total_refund)
            return response
        return Response({
            "count": len(page),
            "results": serializer.data,
            "total_refund": str(total_refund),
        })


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
