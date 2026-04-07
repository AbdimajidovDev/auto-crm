from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.core.exceptions import ValidationError

from apps.transfer.serializers import TransferCreateSerializer
from apps.transfer.services import TransferService


@extend_schema(
    tags=["Transfer"],
    summary="- Transfer yaratish.",
)
class TransferCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TransferCreateSerializer

    def post(self, request):
        serializer = TransferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            transfer = TransferService.create_transfer(
                from_store=serializer.validated_data["from_store"],
                to_store=serializer.validated_data["to_store"],
                product_id=serializer.validated_data["product"],
                quantity=serializer.validated_data["quantity"],
                user=request.user
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response({"id": transfer.id, "status": transfer.status}, status=201)



@extend_schema(
    tags=["Transfer"],
    summary="- Transferni qabul qilish.",
)
class TransferApproveAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            transfer = TransferService.approve_transfer(
                transfer_id=pk,
                user=request.user
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response({"status": "approved"})



@extend_schema(
    tags=["Transfer"],
    summary="- Transfer bekor qilish.",
)
class TransferRejectAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        transfer = TransferService.reject_transfer(
            transfer_id=pk,
            user=request.user
        )
        return Response({"status": "rejected"})
