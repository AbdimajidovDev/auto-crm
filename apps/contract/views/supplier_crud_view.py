from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from django.shortcuts import get_object_or_404

from apps.contract.models import Supplier, StockEntry, SupplierTransaction
from apps.contract.serializers import (
    SupplierCreateSerializer,
    SupplierGetSerializer, SupplierSerializer, SupplierListSerializer,
)
from apps.contract.services import SupplierService

from decimal import Decimal

from django.db import models
from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView


class SupplierPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def _supplier_queryset(search: str | None = None, is_active: str | None = None):
    """
    Barcha supplier so'rovlari uchun yagona, optimallashtirilgan queryset.

    Annotatsiyalar:
    - total_purchase_amount : barcha StockEntry.total_amount yig'indisi.
    - total_debt            : jami kirim (INVENTORY_IN) - jami to'lov (PAYMENT).
                              get_total_debt() bilan aynan bir xil mantiq,
                              lekin Python sikli o'rniga bitta SQL'da hisoblanadi.

    Eslatma:
    - Subquery ishlatildi chunki aggregate + filter kombinatsiyasi ko'p JOIN
      keltirib chiqaradi; Subquery har bir supplier uchun bitta correlated
      SELECT bajaradi va indeksdan to'liq foydalanadi.
    - `only()` keraksiz ustunlarni DB'dan tortib olishdan saqlaydi.
    """
    # --- jami xarid (StockEntry.total_amount yig'indisi) ---
    total_purchase_subquery = (
        StockEntry.objects.filter(supplier=OuterRef("pk"))
        .values("supplier")
        .annotate(total=Sum("total_amount"))
        .values("total")
    )

    # --- INVENTORY_IN tranzaksiyalar yig'indisi (qarz hosil qiluvchi) ---
    debt_in_subquery = (
        SupplierTransaction.objects.filter(
            supplier=OuterRef("pk"),
            type=SupplierTransaction.TransactionType.INVENTORY_IN,
        )
        .values("supplier")
        .annotate(total=Sum("amount"))
        .values("total")
    )

    # --- PAYMENT tranzaksiyalar yig'indisi (qarzni kamaytiruvchi) ---
    debt_paid_subquery = (
        SupplierTransaction.objects.filter(
            supplier=OuterRef("pk"),
            type=SupplierTransaction.TransactionType.PAYMENT,
        )
        .values("supplier")
        .annotate(total=Sum("amount"))
        .values("total")
    )

    zero = Value(Decimal("0.00"), output_field=DecimalField())

    qs = (
        Supplier.objects.only(
            "id", "name", "description", "address",
            "phone_number", "inn", "is_active",
        )
        .annotate(
            total_purchase_amount=Coalesce(
                Subquery(total_purchase_subquery, output_field=DecimalField()),
                zero,
            ),
            # total_debt = jami kirim - jami to'lov  (manfiy bo'lmaydi)
            _debt_in=Coalesce(
                Subquery(debt_in_subquery, output_field=DecimalField()),
                zero,
            ),
            _debt_paid=Coalesce(
                Subquery(debt_paid_subquery, output_field=DecimalField()),
                zero,
            ),
        )
        .annotate(
            # Ikkinchi .annotate() — birinchidagi annotatsiyalarga murojaat qilish uchun
            total_debt=models.ExpressionWrapper(
                models.F("_debt_in") - models.F("_debt_paid"),
                output_field=DecimalField(max_digits=15, decimal_places=2),
            )
        )
        .order_by("name")
    )

    if search:
        # name, phone_number, inn bo'yicha qidiruv.
        # inn va phone_number ustunlari db_index=True — tez ishlaydi.
        from django.db.models import Q
        qs = qs.filter(
            Q(name__icontains=search)
            | Q(phone_number__icontains=search)
            | Q(inn__icontains=search)
        )

    if is_active is not None:
        qs = qs.filter(is_active=is_active.lower() == "true")

    return qs


@extend_schema(
    tags=["Supplier"],
    summary="Taminotchilar ro'yxati (search, pagination, jami xarid summasi).",
    parameters=[
        OpenApiParameter(
            name="search",
            description="Ism, telefon yoki INN bo'yicha qidirish.",
            required=False,
            type=str,
        ),
        OpenApiParameter(
            name="is_active",
            description="Faol/nofaol filtr: true | false",
            required=False,
            type=str,
        ),
        OpenApiParameter(
            name="page",
            description="Sahifa raqami.",
            required=False,
            type=int,
        ),
        OpenApiParameter(
            name="page_size",
            description="Bir sahifadagi elementlar soni (max 100).",
            required=False,
            type=int,
        ),
    ],
)
class SupplierListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierListSerializer
    pagination_class = SupplierPagination

    def get(self, request):
        search = request.query_params.get("search", "").strip() or None
        is_active = request.query_params.get("is_active", None)

        qs = _supplier_queryset(search=search, is_active=is_active)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)

        serializer = SupplierListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


@extend_schema(
        tags=["Supplier"],
        summary="- Taminotchi yaratish.",
    )
class SupplierCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierCreateSerializer


    def post(self, request):
        serializer = SupplierCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            supplier = SupplierService.create_supplier(
                request_user=request.user,
                data=serializer.validated_data
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response(
            SupplierGetSerializer(supplier).data,
            status=status.HTTP_201_CREATED
        )


class SupplierDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierCreateSerializer

    @extend_schema(
        tags=["Supplier"],
        summary="- ID orqali bitta Taminotchi malumotlarini olish.",
    )
    def get(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)
        serializer = SupplierSerializer(supplier, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Supplier"],
        summary="- Taminotchini tahrirlash.",
    )
    def put(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)

        serializer = self.serializer_class(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        try:
            supplier = SupplierService.update_supplier(
                request_user=request.user,
                instance=supplier,
                data=serializer.validated_data
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response(SupplierGetSerializer(supplier).data)

    @extend_schema(
        tags=["Supplier"],
        summary="- Taminotchini o'chirish.",
    )
    def delete(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)

        try:
            SupplierService.delete_supplier(
                request_user=request.user,
                instance=supplier
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response(status=status.HTTP_204_NO_CONTENT)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Supplier list uchun pagination va `only()` qo'shish]
# ═══════════════════════════════
