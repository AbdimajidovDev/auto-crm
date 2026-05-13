from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

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

    def get(self,request):
        # ⚠️ MUAMMO [KRITIK]: Queryset modeli `CustomerDebt`, serializer Meta modeli esa `Payment`.
        # Sabab: serializer maydonlari va enum/type ma'nosi boshqa modelga tegishli.
        # Natija: noto'g'ri response, runtime xato yoki qarz/payment ma'lumotlari aralashib ketishi mumkin.
        # ✅ YECHIM:
        # serializer_class = CustomerDebtListSerializer
        # qs = CustomerDebt.objects.select_related("customer", "sale", "sale__store").order_by("-created_at")
        # ⚠️ MUAMMO [KRITIK/PERFORMANCE]: Filtrsiz `.all()` va pagination yo'q.
        # Sabab: barcha qarz yozuvlari birdan qaytariladi, customer/sale FK select_related qilinmagan.
        # Natija: katta jadvalda N+1 va memory/latency oshishi yuzaga keladi.
        # ✅ YECHIM:
        # qs = CustomerDebt.objects.select_related("customer", "sale").order_by("-created_at")
        # MUAMMO / N+1: `PayDebtListSerializer` Meta.model = `Payment`, lekin queryset `CustomerDebt`.
        # Bu mantiqiy nomuvofiqlik va noto'g'ri serializer maydonlari (type qiymatlari boshqacha) xavfini tug'diradi.
        # Shuningdek `customer` uchun `select_related("customer", "sale")` qo'shmasa N+1 chiqadi.
        qs = CustomerDebt.objects.all()
        serializer = self.serializer_class(qs, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


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
            payment_type=data["type"]
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
        # ⚠️ MUAMMO [PERFORMANCE]: Detailda FKlar `select_related` qilinmagan.
        # Sabab: serializer customer nomiga murojaat qiladi.
        # Natija: detailda ham qo'shimcha query ishlaydi.
        # ✅ YECHIM:
        # qs = get_object_or_404(CustomerDebt.objects.select_related("customer", "sale"), pk=pk)
        qs = get_object_or_404(CustomerDebt, pk=pk)
        serializer = self.serializer_class(qs)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 2
# Performance muammolari: 2
# Arxitektura muammolari: 0
# Umumiy baho: 4 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [PayDebtListAPIView serializer/queryset model nomuvofiqligini tuzatish]
# ═══════════════════════════════
