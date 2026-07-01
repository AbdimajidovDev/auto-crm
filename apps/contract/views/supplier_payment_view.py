from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contract.models import SupplierTransaction, Supplier
from apps.contract.serializers.supplier_payment_serializer import SupplierPaymentSerializer, \
    SupplierPaymentListSerializer
from apps.contract.services import SupplierService, SupplierPaymentService
from apps.contract.models import StockEntry


@extend_schema(
    tags=["Stock Entry"],
    summary="Taminotchiga to'lov qilish (Qarzni uzish).",
)
class SupplierPaymentListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SupplierPaymentListSerializer

    def get(self, request, entry_id):
        # ✅ YAXSHI: `entry` FK bo'yicha filtrlangan — full-table scan yo'q, `entry_id` FK indeksidan foydalanadi.
        # Bitta kirim tranzaksiyalari soni kichik, `SupplierPaymentListSerializer` esa supplier/entry ni
        # PK id sifatida qaytaradi (related obyekt yuklanmaydi) — N+1 yo'q.
        # ⚠️ MUAMMO [PERF]: Ro'yxat paginationsiz. Bitta entry uchun tranzaksiyalar kam bo'lgani uchun
        # xavf past, lekin nazariy jihatdan chegara yo'q.
        # ⚠️ MUAMMO [PERF]: `entry` obyekti faqat mavjudligini tekshirish uchun olinadi, ammo query
        # `filter(entry=entry)` da yana ishlatiladi — `filter(entry_id=entry_id)` qilinsa `get_object_or_404`
        # ortiqcha bo'lardi (2 query → 1). Agar validatsiya (404) kerak bo'lsa hozirgi holat maqbul.
        # ✅ YECHIM (ixtiyoriy): qs = SupplierTransaction.objects.filter(entry_id=entry_id)
        entry = get_object_or_404(StockEntry, pk=entry_id)
        qs = SupplierTransaction.objects.filter(entry=entry)
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
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        payment = SupplierPaymentService.make_payment(
            supplier=serializer.validated_data["supplier"],
            entry=serializer.validated_data["entry"],
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


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# GET (SupplierPaymentListAPIView): entry bo'yicha filtrlangan, N+1 yo'q; paginationsiz (kichik hajm — past xavf).
# POST (SupplierPaymentAPIView): faqat yozuv, query performance muammosi yo'q.
# Kritik muammolar soni: 0
# Performance muammolari: 1 (paginationsiz ro'yxat + ortiqcha get_object_or_404 query)
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [filter(entry_id=entry_id) bilan 1 query'ga tushirish + kerak bo'lsa pagination]
# ═══════════════════════════════