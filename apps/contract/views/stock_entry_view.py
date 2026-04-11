from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.core.exceptions import ValidationError

from apps.contract.models import StockEntry
from apps.contract.serializers import (
    StockEntryCreateSerializer,
    StockEntryListSerializer,
)
from apps.contract.services import StockEntryService
from apps.common.permissions import IsSuperUser


@extend_schema(
    tags=["Stock Entry"],
    summary="Omborga kirim tarixini ko'rish.",
)
class StockEntryListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StockEntryListSerializer

    def get(self, request):
        qs = StockEntry.objects.all()
        serializer = self.serializer_class(qs, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Stock Entry"],
    summary="Omborga kirim qilish.",
)
class StockEntryCreateAPIView(APIView):
    permission_classes = [IsSuperUser]
    serializer_class = StockEntryCreateSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            entry = StockEntryService.create_entry(
                supplier=serializer.validated_data["supplier"],
                store=serializer.validated_data["store"],
                paid_amount=serializer.validated_data["paid_amount"],
                items=serializer.validated_data["items"],
                user=request.user
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response({
            'status': 'success',
            "id": entry.id,
            "items_count": entry.items.count()
        }, status=status.HTTP_201_CREATED)
