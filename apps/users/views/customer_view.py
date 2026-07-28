from django.db import models
from django.db.models import Sum, Case, When, F, Prefetch
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from rest_framework import generics, permissions, status
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.paginations import StandardPagination
from apps.debts.models import CustomerDebt
from apps.sales.models import Sale, SaleItem
from apps.users.models.customers import Customer
from apps.users.serializers.customer_serializer import (
    _debt_subquery,
    CustomerBriefSerializer,
    CustomerWriteSerializer,
    CustomerListSerializer, CustomerSerializer,
)


# ─────────────────────────────────────────────
# UMUMIY QUERYSET
# ─────────────────────────────────────────────

def _customer_queryset():
    """
    Barcha Customer viewlari uchun yagona optimallashtirilgan queryset.

    SQL soni:
      1 → Customer (asosiy)
      1 → debts + sale__store (prefetch)
      1 → sales + store (prefetch)
      1 → sales__items + product (prefetch)
      Jami: ~4 SQL, mijozlar sonidan mustaqil.
    """
    # ✅ YAXSHI: Customer queryset yagona helperga chiqarilgan va sales/items/debts prefetch qilingan.
    sales_qs = (
        Sale.objects
        .select_related("store")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=SaleItem.objects.select_related("product"),
            )
        )
    )

    return (
        Customer.objects
        .prefetch_related(
            Prefetch("sales", queryset=sales_qs),
            Prefetch("debts", queryset=CustomerDebt.objects.select_related("sale__store")),
        )
        .annotate(total_debt=_debt_subquery())
    )


# ─────────────────────────────────────────────
# VIEWS
# ─────────────────────────────────────────────
from decimal import Decimal

from django.db import models
from django.db.models import DecimalField, OuterRef, Prefetch, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, permissions
from rest_framework.filters import OrderingFilter, SearchFilter

from apps.sales.models import Sale, SaleItem

# ------------------------------------------------------------------ #
#  Queryset helper                                                     #
# ------------------------------------------------------------------ #
def _customer_aggregate_queryset():
    """
    Ro'yxat (jadval) uchun AGREGAT queryset — og'ir prefetchlarsiz.

    Sales/debts prefetchlari bu yerda YO'Q: ular faqat detail view
    (`_customer_queryset`) uchun kerak. Ro'yxat shu tufayli katta bazada ham
    ~1 SQL + 3 subquery bilan javob beradi.

    Annotatsiyalar
    ──────────────
    total_purchase_amount
        Mijoz bilan bog'liq barcha Sale.total_amount yig'indisi.
        Subquery ishlatildi → bitta correlated SELECT, N+1 yo'q.

    total_debt
        CustomerDebt modeli orqali:
            _debt_in   = type="i" (kirim/qarz hosil qiluvchi) yig'indisi
            _debt_paid = type="p" (to'lov / qarzni kamaytiradigan) yig'indisi
            total_debt = _debt_in - _debt_paid

        Supplier logikasi bilan aynan bir xil pattern.
        Agar loyihangizda total_debt boshqacha hisoblanayotgan bo'lsa
        (masalan, Sale.total_amount - Sale.paid_amount), quyidagi
        `# ALT:` blokini ochib ishlatish mumkin.

    Prefetch
    ────────
    sales → store          : select_related → JOIN (1 ta qo'shimcha SQL yo'q)
    sales → items → product: W ta Prefetch → jami 3 ta qo'shimcha SQL
    debts → sale → store   : store_debts uchun Python guruhlash
    """
    zero = Value(Decimal("0.00"), output_field=DecimalField())

    # --- jami xarid summasi ---
    total_purchase_subquery = (
        Sale.objects.filter(customer=OuterRef("pk"))
        .exclude(status=Sale.Status.RETURNED)          # qaytarilgan sotuvlarni hisobga olmaydi
        .values("customer")
        .annotate(total=Sum("total_amount"))
        .values("total")
    )

    # --- qarz hosil qiluvchi tranzaksiyalar (CustomerDebt type="i" — INCREASE) ---
    debt_in_subquery = (
        CustomerDebt.objects.filter(
            customer=OuterRef("pk"),
            type=CustomerDebt.Type.INCREASE,
        )
        .values("customer")
        .annotate(total=Sum("amount"))
        .values("total")
    )

    # --- to'lov tranzaksiyalari (CustomerDebt type="d" — DECREASE) ---
    # DIQQAT: avval bu yerda type="p" yozilgan edi — bunday tur mavjud emas
    # (CustomerDebt.Type: "i"=Increase, "d"=Decrease). Natijada to'lovlar 0 bo'lib,
    # ro'yxatdagi total_debt to'lovlarni ayirmasdan oshiq ko'rsatilardi.
    debt_paid_subquery = (
        CustomerDebt.objects.filter(
            customer=OuterRef("pk"),
            type=CustomerDebt.Type.DECREASE,
        )
        .values("customer")
        .annotate(total=Sum("amount"))
        .values("total")
    )

    # ALT: Agar CustomerDebt modeli yo'q bo'lsa va qarz Sale orqali hisoblanayotgan bo'lsa:
    # total_debt_subquery = (
    #     Sale.objects.filter(customer=OuterRef("pk"))
    #     .exclude(status=Sale.Status.RETURNED)
    #     .values("customer")
    #     .annotate(debt=Sum(models.F("total_amount") - models.F("paid_amount")))
    #     .values("debt")
    # )

    return (
        Customer.objects.only("id", "full_name", "phone_number")
        .annotate(
            total_purchase_amount=Coalesce(
                Subquery(total_purchase_subquery, output_field=DecimalField()),
                zero,
            ),
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
            # Ikkinchi .annotate() — birinchi annotatsiyalarga murojaat qilish uchun
            total_debt=models.ExpressionWrapper(
                models.F("_debt_in") - models.F("_debt_paid"),
                output_field=DecimalField(max_digits=20, decimal_places=2),
            )

            # ALT (CustomerDebt yo'q bo'lsa):
            # total_debt=Coalesce(
            #     Subquery(total_debt_subquery, output_field=DecimalField()),
            #     zero,
            # ),
        )
    )


def _customer_queryset():
    """
    Detail view uchun TO'LIQ queryset: agregatlar + sales/items/debts prefetch.
    Bitta mijoz uchun ishlatiladi — prefetchlar arzon.
    """
    items_prefetch = Prefetch(
        "items",
        queryset=SaleItem.objects.select_related("product").only(
            "id", "sale_id", "product_id", "product__name",
            "quantity", "total_price",
        ),
    )

    sales_prefetch = Prefetch(
        "sales",
        queryset=Sale.objects.select_related("store")
        .only(
            "id", "customer_id", "store_id", "store__name",
            "total_amount", "paid_amount", "status", "created_at",
        )
        .prefetch_related(items_prefetch)
        .order_by("-created_at"),
    )

    debts_prefetch = Prefetch(
        "debts",
        queryset=CustomerDebt.objects.select_related("sale__store").only(
            "id", "customer_id", "amount", "type",
            "sale_id", "sale__store_id", "sale__store__name",
        ),
    )

    return _customer_aggregate_queryset().prefetch_related(sales_prefetch, debts_prefetch)


# ------------------------------------------------------------------ #
#  View                                                                #
# ------------------------------------------------------------------ #
@extend_schema_view(
    get=extend_schema(
        tags=["Customer"],
        summary="Mijozlar ro'yxati — search, ordering, pagination, jami xarid va qarz.",
        parameters=[
            OpenApiParameter(
                "search", OpenApiTypes.STR,
                description="Ism yoki telefon bo'yicha qidirish.",
            ),
            OpenApiParameter(
                "ordering", OpenApiTypes.STR,
                description=(
                    "Tartiblash: full_name, -full_name, "
                    "total_debt, -total_debt, "
                    "total_purchase_amount, -total_purchase_amount"
                ),
            ),
            OpenApiParameter("page",  OpenApiTypes.INT, description="Sahifa raqami."),
            OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni (maksimum 100)."),
            OpenApiParameter(
                "brief", OpenApiTypes.BOOL,
                description="1 bo'lsa minimal rejim: faqat id, full_name, phone_number "
                            "(POS dropdown uchun — agregatlar hisoblanmaydi).",
            ),
            OpenApiParameter(
                "debt", OpenApiTypes.STR,
                description="Qarz bo'yicha filtr: `with_debt` (qarzi bor) yoki "
                            "`no_debt` (qarzi yo'q). Filtrsiz — hammasi.",
            ),
        ],
    )
)
class CustomerListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = CustomerListSerializer
    pagination_class   = StandardPagination
    filter_backends    = [SearchFilter, OrderingFilter]
    search_fields      = ["full_name", "phone_number"]
    ordering_fields    = [
        "full_name",
        "total_debt",
        "total_purchase_amount",   # yangi — xarid summasi bo'yicha tartiblash
    ]
    ordering = ["full_name"]

    def _is_brief(self) -> bool:
        """?brief=1 — POS (sotuv) dropdowni uchun minimal rejim."""
        return str(self.request.query_params.get("brief", "")).lower() in ("1", "true", "yes")

    def get_serializer_class(self):
        return CustomerBriefSerializer if self._is_brief() else CustomerListSerializer

    def get_queryset(self):
        if self._is_brief():
            # Eng yengil yo'l: JOIN ham, subquery ham yo'q — dropdown uchun yetarli.
            # Agregat ustunlar yo'qligi sababli ordering ham cheklanadi.
            self.ordering_fields = ["full_name"]
            return Customer.objects.only("id", "full_name", "phone_number")
        # Jadval rejimi: agregatlar bor, lekin sales/debts prefetchlari YO'Q —
        # to'liq tarix faqat detail (/customers/<id>/) endpointida qaytadi.
        qs = _customer_aggregate_queryset()

        # Qarz filtri SERVER tomonida qo'llanadi. Ilgari frontend uni faqat
        # joriy sahifaga qo'llardi — 250 mijozdan 40 tasi qarzdor bo'lsa,
        # "Qarzi bor" filtri 1-sahifadagi 2 tasini ko'rsatib, pager esa
        # baribir 25 sahifa deb turardi.
        debt_filter = (self.request.query_params.get("debt") or "").lower()
        if debt_filter == "with_debt":
            qs = qs.filter(total_debt__gt=0)
        elif debt_filter == "no_debt":
            qs = qs.filter(total_debt__lte=0)

        return qs

    def list(self, request, *args, **kwargs):
        """Javobga BARCHA (nafaqat sahifadagi) mijozlar qarzi jamini qo'shadi."""
        response = super().list(request, *args, **kwargs)

        if not self._is_brief() and isinstance(response.data, dict):
            aggregate = self.filter_queryset(self.get_queryset()).aggregate(
                total_debt_sum=Coalesce(
                    Sum("total_debt"),
                    Value(Decimal("0"), output_field=DecimalField(max_digits=20, decimal_places=2)),
                )
            )
            response.data["total_debt_sum"] = aggregate["total_debt_sum"]

        return response



# @extend_schema_view(
#     get=extend_schema(
#         tags=["customer"],
#         summary="Mijozlar ro'yxati",
#         parameters=[
#             OpenApiParameter("search", OpenApiTypes.STR,
#                              description="Ism yoki telefon bo'yicha qidirish"),
#             OpenApiParameter("ordering", OpenApiTypes.STR,
#                              description="Tartiblash: full_name, -full_name, total_debt, -total_debt"),
#             OpenApiParameter("page", OpenApiTypes.INT, description="Sahifa raqami"),
#             OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni"),
#         ],
#     ),
# )
# class CustomerListView(generics.ListAPIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = CustomerSerializer
#     pagination_class = StandardPagination
#     filter_backends = [SearchFilter, OrderingFilter]
#     search_fields = ["full_name", "phone_number"]
#     ordering_fields = ["full_name", "total_debt"]
#     ordering = ["full_name"]
#
#     def get_queryset(self):
#         return _customer_queryset()


@extend_schema(
    tags=["customer"],
    summary="Mijoz yaratish",
)
class CustomerCreateView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    # Yaratish uchun faqat full_name, phone_number kerak.
    serializer_class = CustomerWriteSerializer


@extend_schema_view(
    get=extend_schema(tags=["customer"], summary="Mijoz ma'lumotlari"),
    put=extend_schema(tags=["customer"], summary="Mijozni yangilash"),
    delete=extend_schema(tags=["customer"], summary="Mijozni o'chirish"),
)
class CustomerDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    O'zgartirishlar:

    1. APIView → generics.RetrieveUpdateDestroyAPIView
       - get/put/delete qo'lda yozilmaydi.

    2. get_object_or_404(Customer, pk=pk) prefetchsiz edi →
       get_queryset() orqali optimallashtirilgan queryset ishlatiladi,
       DRF get_object() avtomatik pk bo'yicha filter qiladi.

    3. put → 201 Created (NOTO'G'RI) → 200 OK (TO'G'RI).
       201 faqat yangi resurs yaratilganda ishlatiladi.

    4. partial=True — PUT ham partial ishlaydi (PATCH kabi),
       shu sababli alohida PATCH endpointi shart emas.
    """
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None  # detail viewda pagination shart emas

    def get_queryset(self):
        return _customer_queryset()

    def get_serializer_class(self):
        # GET → to'liq ma'lumot (total_debt, store_debts, sales)
        # PUT → faqat full_name, phone_number
        if self.request.method in ("PUT", "PATCH"):
            return CustomerWriteSerializer
        return CustomerSerializer

    def update(self, request, *args, **kwargs):
        # partial=True — PUT so'rovida ham barcha maydon majburiy emas.
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        # Moliyaviy tarixi bor mijozni o'chirib bo'lmaydi: qarz va to'lov
        # yozuvlari PROTECT bilan bog'langan, sotuvlar esa SET_NULL bo'lgani
        # uchun "egasiz" qolib, debitorlik jimgina yo'qolardi.
        instance = self.get_object()

        if instance.debts.exists() or instance.payments.exists():
            return Response(
                {"detail": "Bu mijozda qarz yoki to'lov tarixi bor — o'chirib bo'lmaydi."},
                status=status.HTTP_409_CONFLICT,
            )
        if instance.sales.exists():
            return Response(
                {"detail": "Bu mijozda sotuvlar tarixi bor — o'chirib bo'lmaydi."},
                status=status.HTTP_409_CONFLICT,
            )

        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



# from django.db import models
# from django.db.models import Sum, Case, When, F
# from drf_spectacular.utils import extend_schema
# from rest_framework import generics, permissions, status
# from rest_framework.generics import get_object_or_404
# from rest_framework.response import Response
# from rest_framework.views import APIView
#
# from apps.users.models.customers import Customer
# from apps.users.serializers.customer_serializer import CustomerSerializer
#
#
# @extend_schema(
#     tags=['customer'],
#     summary="Mijozlar ro'yxati",
# )
# class CustomerListView(generics.ListAPIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = CustomerSerializer
#     # queryset = Customer.objects.prefetch_related('sales', 'debts__sale__store').all()
#
#     # views.py da querysetni quyidagicha yozish mumkin:
#     # N+1: `CustomerSerializer` ichida `sales` -> `items` -> `product` zanjiri bor — list/detailda
#     # `prefetch_related` (masalan `Prefetch("sales", queryset=Sale.objects.prefetch_related(...))`)
#     # bo'lmasa mijoz soniga proporsional so'rovlar ko'payadi.
#     queryset = Customer.objects.annotate(
#         # Eslatma: bu annotate serializerdagi `get_total_debt` bilan dublikat — serializer hali ham
#         # har bir mijoz uchun alohida aggregate so'rov yuboradi (N+1 + ikki marta hisoblash).
#         annotated_total_debt=Sum(
#             Case(
#                 When(debts__type='i', then=F('debts__amount')),
#                 When(debts__type='d', then=-F('debts__amount')),
#                 default=0,
#                 output_field=models.DecimalField()
#             )
#         )
#     )
#
#
#
# @extend_schema(
#     tags=['customer'],
#     summary="Mijoz yaratish",
# )
# class CustomerCreateView(generics.CreateAPIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = CustomerSerializer
#
#
# @extend_schema(
#     tags=['customer'],
#     summary="Mijoz",
# )
# class CustomerDetailView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
#     serializer_class = CustomerSerializer
#
#     def get(self, request, pk):
#         # N+1: bitta mijoz bo'lsa ham `CustomerSerializer` ichidagi `sales`/`debts` uchun prefetch kerak.
#         customer = get_object_or_404(Customer, pk=pk)
#         serializer = self.serializer_class(customer)
#         return Response(serializer.data, status=status.HTTP_200_OK)
#
#     def put(self, request, pk):
#         customer = get_object_or_404(Customer, pk=pk)
#         serializer = self.serializer_class(customer, data=request.data, partial=True)
#         if serializer.is_valid():
#             serializer.save()
#             return Response(serializer.data, status=status.HTTP_201_CREATED)
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
#
#     def delete(self, request, pk):
#         customer = get_object_or_404(Customer, pk=pk)
#         customer.delete()
#         return Response('Customer successfully deleted!', status=status.HTTP_204_NO_CONTENT)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Customer destroy uchun bog'langan sale/debt himoyasini qo'shish]
# ═══════════════════════════════
