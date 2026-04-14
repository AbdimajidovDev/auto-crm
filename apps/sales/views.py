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


@extend_schema(
    tags=['Sales'],
    summary="Sotuv ro'yxati",
)
class SaleListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SaleListSerializer
    pagination_class = SalePagination

    def get_queryset(self):
        qs = Sale.objects.select_related(
            "store", "customer", "seller"
        ).all()

        request = self.request

        status_param = request.query_params.get("status")
        store_id = request.query_params.get("store")
        seller_id = request.query_params.get("seller")
        customer_id = request.query_params.get("customer")

        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")

        has_debt = request.query_params.get("has_debt")
        search = request.query_params.get("search")

        if status_param:
            qs = qs.filter(status=status_param)

        if store_id:
            qs = qs.filter(store_id=store_id)

        if seller_id:
            qs = qs.filter(seller_id=seller_id)

        if customer_id:
            qs = qs.filter(customer_id=customer_id)

        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)

        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        if has_debt == "true":
            qs = qs.filter(status=Sale.Status.DEBT)

        if has_debt == "false":
            qs = qs.exclude(status=Sale.Status.DEBT)

        if search:
            qs = qs.filter(
                Q(customer__first_name__icontains=search) |
                Q(customer__last_name__icontains=search)
            )

        return qs.order_by("-created_at")

# class SaleListAPIView(APIView):
#     permission_classes = [IsAuthenticated]
#     serializer_class = SaleListSerializer
#
#     def get(self, request):
#         qs = Sale.objects.all()
#         serializer = self.serializer_class(qs, many=True, context={'request': request})
#         return Response(serializer.data, status=status.HTTP_200_OK)


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
        qs = get_object_or_404(Sale, pk=pk)
        serializer = self.serializer_class(qs)
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
