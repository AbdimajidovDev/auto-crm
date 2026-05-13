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
from apps.users.serializers.customer_serializer import CustomerSerializer, _debt_subquery, CustomerWriteSerializer


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

@extend_schema_view(
    get=extend_schema(
        tags=["customer"],
        summary="Mijozlar ro'yxati",
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR,
                             description="Ism yoki telefon bo'yicha qidirish"),
            OpenApiParameter("ordering", OpenApiTypes.STR,
                             description="Tartiblash: full_name, -full_name, total_debt, -total_debt"),
            OpenApiParameter("page", OpenApiTypes.INT, description="Sahifa raqami"),
            OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni"),
        ],
    ),
)
class CustomerListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CustomerSerializer
    pagination_class = StandardPagination
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ["full_name", "phone_number"]
    ordering_fields = ["full_name", "total_debt"]
    ordering = ["full_name"]

    def get_queryset(self):
        # ✅ YAXSHI: List view pagination, search va ordering bilan ishlaydi.
        return _customer_queryset()


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
        # ⚠️ MUAMMO [KRITIK]: Customer delete uchun bog'langan Sale/Debt mavjudligi tekshirilmayapti.
        # Sabab: mijoz tarixiy sotuv yoki qarz yozuvlarida ishlatilgan bo'lishi mumkin.
        # Natija: ProtectedError, 500 response yoki tarixiy audit yo'qolishi xavfi paydo bo'ladi.
        # ✅ YECHIM:
        # if instance.sales.exists() or instance.debts.exists():
        #     raise ValidationError("Bu mijoz tizimda ishlatilgan, o'chirib bo'lmaydi")
        # 204 No Content — standart, lekin string body bilan emas (RFC bo'yicha body bo'lmasligi kerak).
        instance = self.get_object()
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
