from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contract.models import SupplierTransaction
from apps.contract.serializers.supplier_payment_serializer import SupplierPaymentSerializer, \
    SupplierPaymentListSerializer
from apps.contract.services import SupplierService, SupplierPaymentService




@extend_schema(
    tags=["Stock Entry"],
    summary="Taminotchiga to'lov qilish (Qarzni uzish).",
)
class SupplierPaymentListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierPaymentListSerializer

    def get(self, request):
        qs = SupplierTransaction.objects.all()
        serializer = self.serializer_class(qs, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Stock Entry"],
    summary="Taminotchiga to'lov qilish (Qarzni uzish).",
)
class SupplierPaymentAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierPaymentSerializer

    def post(self, request):
        serializer = SupplierPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        payment = SupplierPaymentService.make_payment(
            supplier=serializer.validated_data["supplier"],
            amount=serializer.validated_data["amount"],
            note=serializer.validated_data.get("note"),
            user=request.user
        )

        return Response({
            "status": "success",
            "message": "To'lov muvaffaqiyatli qabul qilindi",
            "transaction_id": payment.id,
            "amount": payment.amount
        }, status=status.HTTP_201_CREATED)