from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.sales.models import SaleReturn
from apps.sales.serializers import SaleReturnCreateSerializer, SaleReturnListSerializer
from apps.sales.services.sale_return_service import SaleReturnService




@extend_schema(
    tags=['Sales'],
    summary="Sotuvni qaytarish."
)
class SaleReturnListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SaleReturnListSerializer

    def get(self, request):
        sale_return = SaleReturn.objects.all()
        serializer = self.serializer_class(sale_return, many=True)
        return Response(serializer.data, status=200)


@extend_schema(
    tags=['Sales'],
    summary="Sotuvni qaytarish."
)
class SaleReturnCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SaleReturnCreateSerializer

    @extend_schema(
        tags=['Sales'],
        summary="Sotuvni qaytarish",
    )
    def post(self, request):

        serializer = SaleReturnCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return_obj = SaleReturnService.create_return(
            user=request.user,
            data=serializer.validated_data
        )

        return Response({
            "return_id": return_obj.id,
            "refund": return_obj.total_refund
        }, status=201)
