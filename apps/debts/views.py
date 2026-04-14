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
