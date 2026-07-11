from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics

from apps.common.paginations import StandardPagination
from apps.debts.models import CustomerDebt
from apps.sales.models import Sale, SaleItem, Payment
from apps.sales.serializers import SaleCreateSerializer, SaleListSerializer, CustomerDebtListSerializer
from apps.sales.services import SaleService
from django.db.models import Q

from apps.sales.services import CustomerDebtService
from apps.sales.filters import SaleFilter

from django.db.models import (
    Case, DecimalField, F, OuterRef,
    Prefetch, Subquery, Sum, Value, When,
)
from django.db.models.functions import Coalesce
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics, serializers
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated

# ─────────────────────────────────────────────
# YORDAMCHI SUBQUERYLAR
# ─────────────────────────────────────────────

def _debt_increase_subquery() -> Subquery:
    """
    Har bir Sale uchun umumiy qarzdorlik (type='i') ni Subquery orqali hisoblaydi.

    Nima uchun Subquery?
      Avvalgi kod:
        qs.annotate(total_increase=Coalesce(Sum(Case(When(debt_records__type='i', ...)))))
      Muammo:
        Sale → items (ko'p) va Sale → debt_records (ko'p) bir vaqtda JOIN bo'lganda
        kartezian ko'payish yuz beradi:
          3 ta item × 2 ta debt_record = 6 qator → Sum ikki baravar katta chiqadi ❌

      Subquery esa asosiy querydan AJRALGAN ishlaydi — kartezian yo'q,
      har Sale uchun to'g'ri qiymat qaytaradi ✅
    """
    return Coalesce(
        Subquery(
            CustomerDebt.objects.filter(sale=OuterRef("pk"), type="i")
            .values("sale")
            .annotate(total=Sum("amount"))
            .values("total")[:1],
            output_field=DecimalField(),
        ),
        Value(0, output_field=DecimalField()),
    )


def _debt_decrease_subquery() -> Subquery:
    """Har bir Sale uchun qarz kamayishi (type='d') ni Subquery orqali hisoblaydi."""
    return Coalesce(
        Subquery(
            CustomerDebt.objects.filter(sale=OuterRef("pk"), type="d")
            .values("sale")
            .annotate(total=Sum("amount"))
            .values("total")[:1],
            output_field=DecimalField(),
        ),
        Value(0, output_field=DecimalField()),
    )


# ─────────────────────────────────────────────
# VIEW
# ─────────────────────────────────────────────

@extend_schema(
    tags=["Sales"],
    summary="Sotuv ro'yxati",
    parameters=[
        OpenApiParameter("search", OpenApiTypes.STR,
                         description="Mijoz ismi bo'yicha qidirish"),
        OpenApiParameter("status", OpenApiTypes.STR,
                         description="Holat: paid, partial, debt, r"),
        OpenApiParameter("store", OpenApiTypes.INT,
                         description="Do'kon ID"),
        OpenApiParameter("customer", OpenApiTypes.INT,
                         description="Mijoz ID"),
        OpenApiParameter("date_from", OpenApiTypes.DATE,
                         description="Sana oralig'i boshi (YYYY-MM-DD)"),
        OpenApiParameter("date_to", OpenApiTypes.DATE,
                         description="Sana oralig'i oxiri (YYYY-MM-DD)"),
        OpenApiParameter("ordering", OpenApiTypes.STR,
                         description="-created_at, total_amount, -total_amount"),
        OpenApiParameter("page", OpenApiTypes.INT, description="Sahifa raqami"),
        OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni"),
    ],
)
class SaleListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SaleListSerializer
    pagination_class = StandardPagination

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SaleFilter
    search_fields = ["customer__full_name"]
    ordering_fields = ["created_at", "total_amount"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user

        # ✅ YAXSHI: `SaleItem` uchun `product` select_related bilan prefetch qilinyapti, item serializer N+1 dan himoyalangan.
        items_qs = SaleItem.objects.select_related("product")

        qs = (
            Sale.objects
            .select_related("store", "customer", "seller")
            .prefetch_related(
                Prefetch("items", queryset=items_qs),
                Prefetch("payments", queryset=Payment.objects.select_related("bank_card").order_by("created_at")),
            )
            .annotate(
                # ✅ YAXSHI: Qarz yig'indilari reverse FK join bilan emas, `Subquery` orqali hisoblangan.
                # Bu `items` va `debt_records` bir vaqtda join bo'lganda kartezian ko'payishdan saqlaydi.
                total_increase=_debt_increase_subquery(),
                total_decrease=_debt_decrease_subquery(),
            )
        )

        # 🔐 PERMISSION: superuser barcha sotuvlarni ko'radi,
        # oddiy foydalanuvchi faqat o'zinikini.
        if not user.is_superuser:
            qs = qs.filter(seller=user)

        return qs



# @extend_schema(
#     tags=['Sales'],
#     summary="Sotuv ro'yxati",
# )
# class SaleListAPIView(generics.ListAPIView):
#     permission_classes = [IsAuthenticated]
#     serializer_class = SaleListSerializer
#     pagination_class = StandardPagination
#
#     filter_backends = [DjangoFilterBackend]
#     filterset_class = SaleFilter
#
#     def get_queryset(self):
#         user = self.request.user
#
#         qs = Sale.objects.select_related(
#             "store", "customer", "seller"
#         ).prefetch_related("items")
#         # N+1 tuzatish: yuqoridagi `items` prefetchiga `Prefetch(..., queryset=SaleItem.objects.select_related("product"))`
#         # qo'shish tavsiya etiladi — `SaleItemSerializer.get_product_name` uchun.
#
#         # 🔐 PERMISSION
#         if not user.is_superuser:
#             qs = qs.filter(seller=user)
#
#         # 🔥 LEDGER BASED DEBT
#         # SQL / mantiq: `Sum` bilan bog'langan reverse FK (`debt_records`) boshqa `annotate`lar
#         # yoki joinlar bilan aralashsa kartezian ko'payish va noto'g'ri yig'indi xavfi bor — murakkab
#         # tarixda subquery yoki alohida hisoblash strategiyasini ko'rib chiqish tavsiya etiladi.
#         qs = qs.annotate(
#
#             total_increase=Coalesce(Sum(
#                 Case(
#                     When(
#                         debt_records__type="i",
#                         then=F("debt_records__amount")
#                     ),
#                     output_field=DecimalField()
#                 )
#             ), Value(0, output_field=DecimalField())),
#
#             total_decrease=Coalesce(Sum(
#                 Case(
#                     When(
#                         debt_records__type="d",
#                         then=F("debt_records__amount")
#                     ),
#                     output_field=DecimalField()
#                 )
#             ), Value(0, output_field=DecimalField())),
#         )
#
#         return qs.order_by("-created_at")


@extend_schema(
    tags=['Sales'],
    summary="Sotuv yaratish",
)
class SaleCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SaleCreateSerializer

    def post(self, request):

        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        sale = SaleService.create_sale(
            user=request.user,
            data=serializer.validated_data
        )

        return Response({
            "sale_id": sale.id,
            "total": sale.total_amount,
            "paid": sale.paid_amount,
            "status": sale.status
        }, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=['Sales'],
    summary="ID orqali Sotuv malumotlarini olish",
)
class SaleDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SaleListSerializer

    def get(self, request, pk):
        qs = Sale.objects.select_related(
            "store", "customer", "seller"
        ).prefetch_related(
            Prefetch("items", queryset=SaleItem.objects.select_related("product")),
            Prefetch("payments", queryset=Payment.objects.select_related("bank_card").order_by("created_at")),
        ).annotate(
        # ⚠️ MUAMMO [KRITIK]: Detail querysetda `items` va `debt_records` aggregate birga ishlatilgan.
        # Sabab: `prefetch_related("items")` productni olib kelmaydi, `Sum(debt_records...)` esa reverse FK joinlarga
        # boshqa joinlar qo'shilsa kartezian ko'payish xavfini saqlab qoladi.
        # Natija: qarz summasi noto'g'ri chiqishi va item product nomida N+1 yuzaga kelishi mumkin.
        # ✅ YECHIM:
        # qs = (
        #     Sale.objects.select_related("store", "customer", "seller")
        #     .prefetch_related(Prefetch("items", queryset=SaleItem.objects.select_related("product")))
        #     .annotate(total_increase=_debt_increase_subquery(), total_decrease=_debt_decrease_subquery())
        # )
        # N+1: bitta sotuvda ham `items__product` prefetch (`Prefetch` + `select_related("product")`) tavsiya.
        # SQL: `debt_records` annotate bilan kartezian ko'payish xavfi (ro'yxat view bilan bir xil).

            total_increase=Coalesce(Sum(
                Case(
                    When(
                        debt_records__type="i",
                        then=F("debt_records__amount")
                    ),
                    output_field=DecimalField()
                )
            ), Value(0, output_field=DecimalField())),

            total_decrease=Coalesce(Sum(
                Case(
                    When(
                        debt_records__type="d",
                        then=F("debt_records__amount")
                    ),
                    output_field=DecimalField()
                )
            ), Value(0, output_field=DecimalField())),
        )

        sale = get_object_or_404(qs, pk=pk)

        serializer = self.serializer_class(sale)
        return Response(serializer.data, status=status.HTTP_200_OK)


# =============================================================================

@extend_schema(
    tags=['Sales'],
    summary="Qarzdor mijozlar ro'yxati"
)
class CustomerDebtListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CustomerDebtListSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        return CustomerDebtService.get_customer_debts(user=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()

        # ✅ YAXSHI: Qarzdor mijozlar ro'yxati pagination orqali qaytariladi.
        page = self.paginate_queryset(queryset)

        formatted_data = CustomerDebtService.format_debt_response(
            data=page
        )

        return self.get_paginated_response(formatted_data)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [SaleDetailAPIView annotate strategiyasini list viewdagi Subquery bilan bir xil qilish]
# ═══════════════════════════════
