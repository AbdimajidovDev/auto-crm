from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from apps.common.paginations import StandardPagination
from .models import CustomerDebt
from .serializers import PayDebtSerializer, PayDebtListSerializer
from .services import DebtService



@extend_schema(
    tags=['Debts'],
    summary="Payment debt ro'yxati",
)
class PayDebtListAPIView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PayDebtListSerializer
    pagination_class = StandardPagination

    def get(self,request):
        # ✅ BAJARILDI — pagination + N+1 optimizatsiyasi (TransferListAPIView bilan bir xil pattern):
        #
        #   AVVAL:
        #     qs = CustomerDebt.objects.all()
        #     serializer = self.serializer_class(qs, many=True, ...)
        #     return Response(serializer.data, ...)
        #   Muammo: (1) PAGINATION YO'Q — barcha qarz yozuvlari bir javobda qaytardi.
        #     (2) N+1 — `PayDebtListSerializer.get_customer_name` `customer.full_name` ga murojaat qiladi,
        #     select_related bo'lmagani sabab har qator uchun alohida so'rov chiqardi.
        #
        #   HOZIR:
        #     (1) `select_related("customer", "sale")` — customer/sale FK'lari JOIN bilan keladi (N+1 yo'q).
        #     (2) `StandardPagination` — har sahifada 20 qator (`?page=` / `?limit=`).
        #     Tartib determinstik bo'lishi uchun `order_by("-created_at")` qo'shildi (pagination talabi).
        #
        # ⚠️ ESLATMA [KRITIK — LOGIKA, tegilMADI]: bu yerda hamon ARXITEKTURA xatosi bor —
        #   queryset modeli `CustomerDebt`, lekin `PayDebtListSerializer.Meta.model = Payment`.
        #   Bu to'g'rilik (correctness) muammosi; foydalanuvchi topshirig'i bo'yicha LOGIKA o'zgartirilmadi.
        #   Alohida tuzatish kerak: serializer'ni `CustomerDebt` uchun yozish (debts/serializers.py dagi izohga qarang).
        qs = CustomerDebt.objects.select_related("customer", "sale").order_by("-created_at")
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = self.serializer_class(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)


@extend_schema(
    tags=['Debts'],
    summary="Payment debt",
)
class PayDebtAPIView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PayDebtSerializer

    def post(self, request):

        serializer = PayDebtSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        payment = DebtService.pay_debt(
            sale_id=data["sale"],
            amount=data["amount"],
            payment_type=data["type"],
            bank_card=data.get("bank_card"),
        )

        return Response({
            "message": "Debt paid successfully",
            "payment_id": payment.id,
            "amount": payment.amount
        }, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=['Debts'],
    summary="ID orqali qarzdorlik malumotlarini olish."
)
class PayDebtDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PayDebtListSerializer

    def get(self, request, pk):
        # ✅ BAJARILDI — FKlar select_related qilindi:
        #   AVVAL: get_object_or_404(CustomerDebt, pk=pk) → serializer `customer.full_name` ga tekkanda
        #          qo'shimcha so'rov chiqardi.
        #   HOZIR: select_related("customer", "sale") bilan bitta so'rovda keladi. Logika o'zgarmadi.
        qs = get_object_or_404(CustomerDebt.objects.select_related("customer", "sale"), pk=pk)
        serializer = self.serializer_class(qs)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ═══════════════════════════════
# 📊 FAYL XULOSASI (yangilangan)
# Kritik muammolar soni: 1  (avval 2 — pagination/N+1 TUZATILDI; qolgani: serializer/model nomuvofiqligi — LOGIKA, tegilmadi)
# Performance muammolari: 0  (avval 2 — list pagination+select_related va detail select_related TUZATILDI)
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10  (avval 4/10 — perf tuzatildi; correctness bug qolgani uchun 10 emas)
# ✅ BAJARILDI: PayDebtListAPIView → StandardPagination + select_related; PayDebtDetailAPIView → select_related.
# Prioritet bo'yicha birinchi hal qilinishi kerak: [PayDebtListSerializer.Meta.model = Payment ni CustomerDebt ga moslash (correctness)]
# ═══════════════════════════════
