from django.shortcuts import render
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.transfer.services import TransferService


# Create your views here.

@extend_schema(
    tags=["Transfer"],
    summary="- TRansfer qilish.",
)
class StockTransferAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    

    def post(self, request):
        from_store = request.data.get("from_store")
        to_store = request.data.get("to_store")
        product_id = request.data.get("product")
        quantity = int(request.data.get("quantity"))
        barcode = request.data.get("barcode")

        TransferService.transfer(
            from_store_id=from_store,
            to_store_id=to_store,
            product_id=product_id,
            quantity=quantity,
            barcode=barcode
        )

        return Response({"detail": "Transfer successful"})