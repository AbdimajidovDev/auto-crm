from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from django.shortcuts import get_object_or_404

from apps.contract.models import Supplier
from apps.contract.serializers import (
    SupplierCreateSerializer,
    SupplierGetSerializer, SupplierSerializer,
)
from apps.contract.services import SupplierService




@extend_schema(
        tags=["Supplier"],
        summary="- Taminotchilar ro'yxati.",
    )
class SupplierListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierGetSerializer

    def get(self, request):
        # ⚠️ MUAMMO [PERFORMANCE]: Supplier list filtrsiz `.all()` va pagination/cache yo'q.
        # Sabab: barcha supplierlar birdan serializerga beriladi.
        # Natija: supplierlar ko'payganda response sekinlashadi va xotira sarfi oshadi.
        # ✅ YECHIM:
        # suppliers = Supplier.objects.only("id", "name", "inn", "phone").order_by("name")
        # page = self.paginate_queryset(suppliers)
        suppliers = Supplier.objects.all()
        serializer = SupplierGetSerializer(suppliers, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
        tags=["Supplier"],
        summary="- Taminotchi yaratish.",
    )
class SupplierCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierCreateSerializer


    def post(self, request):
        serializer = SupplierCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            supplier = SupplierService.create_supplier(
                request_user=request.user,
                data=serializer.validated_data
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response(
            SupplierGetSerializer(supplier).data,
            status=status.HTTP_201_CREATED
        )


class SupplierDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierCreateSerializer

    @extend_schema(
        tags=["Supplier"],
        summary="- ID orqali bitta Taminotchi malumotlarini olish.",
    )
    def get(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)
        serializer = SupplierSerializer(supplier, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Supplier"],
        summary="- Taminotchini tahrirlash.",
    )
    def put(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)

        serializer = self.serializer_class(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        try:
            supplier = SupplierService.update_supplier(
                request_user=request.user,
                instance=supplier,
                data=serializer.validated_data
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response(SupplierGetSerializer(supplier).data)

    @extend_schema(
        tags=["Supplier"],
        summary="- Taminotchini o'chirish.",
    )
    def delete(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)

        try:
            SupplierService.delete_supplier(
                request_user=request.user,
                instance=supplier
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response(status=status.HTTP_204_NO_CONTENT)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Supplier list uchun pagination va `only()` qo'shish]
# ═══════════════════════════════
