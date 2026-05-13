from django.contrib.auth.models import PermissionsMixin
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.core.exceptions import ValidationError

from apps.transfer.models import StockTransfer, Notification
from apps.transfer.serializers import TransferCreateSerializer, TransferListSerializer, NotificationSerializer
from apps.transfer.services import TransferService


@extend_schema(
    tags=["Transfer"],
    summary="- Transfer yaratish.",
)
class TransferListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TransferListSerializer

    def get(self, request):
        # ⚠️ MUAMMO [KRITIK/PERFORMANCE]: Filtrsiz `.all()` va serializer uchun N+1 query xavfi.
        # Sabab: `TransferListSerializer` `from_store`, `to_store`, `approved_by`, `items`, `items__product`
        # maydonlariga murojaat qiladi, lekin queryset `select_related` / `prefetch_related` bilan tayyorlanmagan.
        # Natija: transferlar ko'payganda har bir qator va item uchun qo'shimcha SQL so'rovlari chiqadi.
        # ✅ YECHIM:
        # transfers = (
        #     StockTransfer.objects
        #     .select_related("from_store", "to_store", "approved_by", "created_by")
        #     .prefetch_related("items__product")
        #     .order_by("-created_at")
        # )
        # N+1: `TransferListSerializer` do'konlar, `approved_by`, `items`, `items__product` ustidan
        # yuradi — querysetda `select_related` / `prefetch_related` bo'lmasa ro'yxat sekinlashadi.
        transfers = StockTransfer.objects.all()
        serializer = self.serializer_class(transfers, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Transfer"],
    summary="- Transfer yaratish.",
)
class TransferCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TransferCreateSerializer

    def post(self, request):
        user = request.user
        serializer = TransferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            # ⚠️ MUAMMO [CLEAN CODE/XAVFSIZLIK]: Production kodda `print` ishlatilgan.
            # Sabab: `validated_data` ichida biznes ma'lumotlar bo'lishi mumkin va stdout nazoratsiz to'lib boradi.
            # Natija: loglarda maxfiy ma'lumot sizishi yoki keraksiz I/O xarajati yuzaga keladi.
            # ✅ YECHIM:
            # logger.debug("Transfer validation passed", extra={"user_id": request.user.id})
            # MUAMMO: productionda `print` — keraksiz stdout va potentsial maxfiy ma'lumot sizishi.
            print('validation data:', serializer.validated_data)
            transfer = TransferService.create_transfer(
                from_store=serializer.validated_data["from_store"],
                to_store=serializer.validated_data["to_store"],
                items_data=serializer.validated_data["items"],
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


@extend_schema(
    tags=["Transfer"],
    summary='Notification'
)
class NotificationListAPIView(APIView):
    # ⚠️ MUAMMO [KRITIK/XAVFSIZLIK]: `permission_classes` berilmagan.
    # Sabab: project default permission sozlamasi ochiq bo'lsa, anonim foydalanuvchi notification endpointga kira oladi.
    # Natija: foydalanuvchi bildirishnomalari yoki mavjudligi haqida ma'lumot sizishi mumkin.
    # ✅ YECHIM:
    # permission_classes = [permissions.IsAuthenticated]
    # XAVFSIZLIK: `permission_classes` berilmagan — default ochiq bo'lishi mumkin; faqat
    # autentifikatsiyalangan foydalanuvchi o'z bildirishnomalarini ko'rishi kerak.
    def get(self, request):
        # ✅ YAXSHI: Bildirishnomalar `request.user` bo'yicha filtrlangan va `[:50]` limit bilan cheklangan.
        qs = Notification.objects.filter(user=request.user).order_by("-created_at")[:50]
        serializer = NotificationSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 2
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [TransferListAPIView querysetini select_related/prefetch_related bilan optimallashtirish, NotificationListAPIView permission_classes qo'shish]
# ═══════════════════════════════
