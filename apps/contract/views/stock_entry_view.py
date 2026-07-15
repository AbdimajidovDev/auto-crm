from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from django.core.exceptions import ValidationError
from django.db.models import Sum, Case, When, F, DecimalField, Value
from django.db.models.functions import Coalesce

from apps.common.paginations import StandardPagination
from apps.contract.filters import StockEntryFilter
from apps.contract.models import StockEntry
from apps.contract.serializers import (
    StockEntryCreateSerializer,
    StockEntryListSerializer,
)
from apps.contract.services import StockEntryService
from apps.contract.permissions import CanCreateStockEntry, allowed_store_ids, ensure_store_access

from django.db.models import (
    Case, DecimalField, F, OuterRef,
    Prefetch, Subquery, Sum, Value, When,
)
from django.db.models.functions import Coalesce
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics, permissions, serializers
from rest_framework.filters import OrderingFilter, SearchFilter
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from drf_spectacular.types import OpenApiTypes

from apps.contract.models import StockEntry, StockEntryItem, SupplierTransaction
from apps.products.models import ProductBatch


# ─────────────────────────────────────────────
# YORDAMCHI SUBQUERYLAR
# ─────────────────────────────────────────────

def _total_in_subquery() -> Subquery:
    """
    Har bir StockEntry uchun kirim summasini Subquery orqali hisoblaydi.

    Nima uchun Subquery?
      Avvalgi kod:
        .annotate(total_in=Coalesce(Sum(Case(When(supplier__transactions__...)))))

      Muammo — ikki darajali JOIN:
        StockEntry → supplier → transactions (+ entry filter)

        StockEntry da items ham bor (prefetch_related("items")):
          3 ta item × 2 ta transaction = 6 qator
          Sum(amount) → 3 MARTA KATTA ❌

      Bundan tashqari supplier__transactions__entry=F("id") filteri
      noto'g'ri ishlashi mumkin — supplier ning BARCHA entry lari
      ko'rinib ketishi xavfi bor.

      Subquery: to'g'ridan entry=OuterRef("pk") filter qiladi,
      kartezian yo'q, har Entry uchun aniq qiymat ✅
    """
    return Coalesce(
        Subquery(
            SupplierTransaction.objects
            .filter(entry=OuterRef("pk"), type="in")
            .values("entry")
            .annotate(total=Sum("amount"))
            .values("total")[:1],
            output_field=DecimalField(),
        ),
        Value(0, output_field=DecimalField()),
    )


def _total_paid_subquery() -> Subquery:
    """
    Har bir StockEntry uchun to'langan summasini Subquery orqali hisoblaydi.
    Xuddi _total_in_subquery() kabi, faqat type="pay".
    """
    return Coalesce(
        Subquery(
            SupplierTransaction.objects
            .filter(entry=OuterRef("pk"), type="pay")
            .values("entry")
            .annotate(total=Sum("amount"))
            .values("total")[:1],
            output_field=DecimalField(),
        ),
        Value(0, output_field=DecimalField()),
    )


# ─────────────────────────────────────────────
# VIEW
# ─────────────────────────────────────────────

@extend_schema_view(
    get=extend_schema(
        tags=["Stock Entry"],
        summary="Omborga kirim tarixi ro'yxati",
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR,
                             description="Yetkazib beruvchi nomi bo'yicha qidirish"),
            OpenApiParameter("supplier", OpenApiTypes.INT, description="Yetkazib beruvchi ID"),
            OpenApiParameter("store", OpenApiTypes.INT, description="Do'kon ID"),
            OpenApiParameter("date_from", OpenApiTypes.DATE, description="Sana boshi (YYYY-MM-DD)"),
            OpenApiParameter("date_to", OpenApiTypes.DATE, description="Sana oxiri (YYYY-MM-DD)"),
            OpenApiParameter("ordering", OpenApiTypes.STR,
                             description="Tartiblash: -created_at, total_amount, -total_amount"),
            OpenApiParameter("page", OpenApiTypes.INT, description="Sahifa raqami"),
            OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni"),
        ],
    ),
)
class StockEntryListAPIView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StockEntryListSerializer
    pagination_class = StandardPagination

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = StockEntryFilter
    search_fields = ["supplier__name"]
    ordering_fields = ["created_at", "total_amount"]
    ordering = ["-created_at"]

    def get_queryset(self):
        """
        SQL rejasi:
          1 → StockEntry (main query) + Subquery × 2 (total_in, total_paid)
          1 → items (Prefetch)
          1 → items → product (select_related)
          1 → product → batches (Prefetch, to_attr="fetched_batches")
          Jami: ~4-5 SQL, entry soni va item sonidan mustaqil.
        """
        # ✅ YAXSHI: StockEntry listda `Subquery` ishlatilgan, reverse FK aggregate kartezian ko'payishdan himoyalangan.
        items_qs = (
            StockEntryItem.objects
            .select_related("product", "entry__store")  # entry.store_id kerak (_get_batch uchun)
            .prefetch_related(
                Prefetch(
                    "product__batches",
                    queryset=ProductBatch.objects.filter(is_active=True).order_by("created_at"),
                    to_attr="fetched_batches",  # serializer xotiradan o'qiydi
                )
            )
        )

        queryset = (
            StockEntry.objects
            .select_related("supplier", "store", "created_by")
            .prefetch_related(
                Prefetch("items", queryset=items_qs),
            )
            .annotate(
                total_in=_total_in_subquery(),
                total_paid=_total_paid_subquery(),
            )
        )

        # Do'kon xodimi faqat o'z do'kon(lar)ining kirimlarini ko'radi
        allowed = allowed_store_ids(self.request.user)
        if allowed is not None:
            queryset = queryset.filter(store_id__in=allowed)

        return queryset


@extend_schema(
    tags=["Stock Entry"],
    summary="Do'konga kirim qilish (superuser — istalgan do'konga, xodim — o'z do'koniga).",
)
class StockEntryCreateAPIView(APIView):
    permission_classes = [CanCreateStockEntry]
    serializer_class = StockEntryCreateSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Do'kon xodimi faqat o'z do'koniga kirim qiladi (superuser — hammasiga)
        ensure_store_access(request.user, serializer.validated_data["store"].id)

        entry = StockEntryService.create_entry(
            supplier=serializer.validated_data["supplier"],
            store=serializer.validated_data["store"],
            cash_amount=serializer.validated_data["cash_amount"],
            card_amount=serializer.validated_data["card_amount"],
            bank_card=serializer.validated_data.get("bank_card"),
            note=serializer.validated_data.get("note", ""),
            items=serializer.validated_data["items"],
            user=request.user
        )

        return Response({
            'status': 'success',
            "id": entry.id,
            "items_count": len(serializer.validated_data["items"]),
            "payment_type": entry.payment_type,
            "paid_amount": entry.paid_amount,
            "debt_amount": entry.debt_amount,
        }, status=status.HTTP_201_CREATED)


# @extend_schema(
#     tags=["Stock Entry"],
#     summary="Omborga kirim tarixini ko'rish.",
# )
# class StockEntryListAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = StockEntryListSerializer
#
#     def get(self, request):
#
#         qs = StockEntry.objects.select_related(
#             "supplier", "store", "created_by"
#         ).prefetch_related(
#             "items"
#         ).annotate(
#         # SQL: `supplier__transactions` ustidan `Sum` — bitta kirimga tegishli bir nechta tranzaksiya
#         # qatorlari join orqali dublikatlanib, `total_in` / `total_paid` noto'g'ri bo'lishi mumkin;
#         # subquery/FILTER yondashuvi bilan tekshirish tavsiya etiladi.
#
#         total_in = Coalesce(Sum(
#             Case(
#                 When(
#                     supplier__transactions__entry=F("id"),
#                     supplier__transactions__type="in",
#                     then=F("supplier__transactions__amount")
#                 ),
#                 output_field=DecimalField()
#             )
#         ), Value(0, output_field=DecimalField())),
#
#         total_paid = Coalesce(Sum(
#             Case(
#                 When(
#                     supplier__transactions__entry=F("id"),
#                     supplier__transactions__type="pay",
#                     then=F("supplier__transactions__amount")
#                 ),
#                 output_field=DecimalField()
#             )
#         ), Value(0, output_field=DecimalField())),
#         ).order_by("-created_at")
#
#         serializer = self.serializer_class(qs, many=True, context={"request": request})
#         return Response(serializer.data, status=status.HTTP_200_OK)

# class StockEntryListAPIView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = StockEntryListSerializer
#
#     def get(self, request):
#         qs = StockEntry.objects.all()
#         serializer = self.serializer_class(qs, many=True, context={"request": request})
#         return Response(serializer.data, status=status.HTTP_200_OK)


# @extend_schema(
#     tags=["Stock Entry"],
#     summary="Omborga kirim qilish.",
# )
# class StockEntryCreateAPIView(APIView):
#     permission_classes = [IsSuperUser]
#     serializer_class = StockEntryCreateSerializer
#
#     def post(self, request):
#         serializer = self.serializer_class(data=request.data)
#         serializer.is_valid(raise_exception=True)
#
#         try:
#             entry = StockEntryService.create_entry(
#                 supplier=serializer.validated_data["supplier"],
#                 store=serializer.validated_data["store"],
#                 paid_amount=serializer.validated_data["paid_amount"],
#                 payment_type=serializer.validated_data["payment_type"],
#                 items=serializer.validated_data["items"],
#                 user=request.user
#             )
#         except ValidationError as e:
#             return Response({"detail": str(e)}, status=400)
#
#         return Response({
#             'status': 'success',
#             "id": entry.id,
#             # ⚠️ MUAMMO [PERFORMANCE]: `entry.items.count()` yangi COUNT query bajaradi.
#             # Sabab: service `items` sonini biladi, lekin response uchun entry reverse managerdan qayta so'ralmoqda.
#             # Natija: create endpointda ortiqcha DB query ishlaydi.
#             # ✅ YECHIM:
#             # "items_count": len(serializer.validated_data["items"])
#             # Qo'shimcha so'rov: `items` allaqachon yuklangan bo'lsa `len(serializer...)`
#             # yoki `created_items_count` kabi maydon bilan cheklash mumkin.
#             "items_count": entry.items.count()
#         }, status=status.HTTP_201_CREATED)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Create response uchun `entry.items.count()` o'rniga validated items uzunligini ishlatish]
# ═══════════════════════════════
