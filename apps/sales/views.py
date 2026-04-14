from drf_spectacular.utils import extend_schema
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics

from apps.sales.models import Sale
from apps.sales.serializers import SaleCreateSerializer, SaleListSerializer, CustomerDebtListSerializer
from apps.sales.services import SaleService
from django.db.models import Q

from apps.sales.utility import SalePagination, DebtPagination

from apps.sales.services import CustomerDebtService
from apps.sales.filters import SaleFilter


from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Sum, Case, When, F, DecimalField, Value
from django.db.models.functions import Coalesce



@extend_schema(
    tags=['Sales'],
    summary="Sotuv ro'yxati",
)
class SaleListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SaleListSerializer
    pagination_class = SalePagination

    filter_backends = [DjangoFilterBackend]
    filterset_class = SaleFilter

    def get_queryset(self):
        user = self.request.user

        qs = Sale.objects.select_related(
            "store", "customer", "seller"
        ).prefetch_related("items")

        # 🔐 PERMISSION
        if not user.is_superuser:
            qs = qs.filter(seller=user)

        # 🔥 LEDGER BASED DEBT
        qs = qs.annotate(

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

        return qs.order_by("-created_at")


@extend_schema(
    tags=['Sales'],
    summary="Sotuv yaratish",
)
class SaleCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SaleCreateSerializer

    def post(self, request):

        serializer = SaleCreateSerializer(data=request.data)
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
        ).prefetch_related("items").annotate(

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
    pagination_class = DebtPagination

    def get_queryset(self):
        return CustomerDebtService.get_customer_debts(user=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()

        page = self.paginate_queryset(queryset)

        formatted_data = CustomerDebtService.format_debt_response(
            data=page
        )

        return self.get_paginated_response(formatted_data)
