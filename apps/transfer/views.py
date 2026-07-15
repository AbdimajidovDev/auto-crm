from django.contrib.auth.models import PermissionsMixin
from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.core.exceptions import ValidationError

from apps.common.paginations import StandardPagination
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

    pagination_class = StandardPagination

    def get(self, request):
        # ✅ BAJARILDI — "juda uzoq loading" muammosi tuzatildi (logika o'zgarmadi):
        #
        #   AVVAL:
        #     transfers = StockTransfer.objects.all()
        #     serializer = self.serializer_class(transfers, many=True)
        #     return Response(serializer.data, ...)
        #   Muammo: (1) PAGINATION YO'Q — migratsiyadan keyin ~38k `StockTransfer` qatorining HAMMASI
        #     bitta javobda, har biriga nested `items` bilan serializatsiya qilinardi (ulkan JSON + xotira).
        #     (2) N+1 — `TransferListSerializer` `from_store.name`, `to_store.name`, `approved_by.full_name`
        #     va `items__product.name/sku` ga murojaat qiladi; queryset optimallashtirilmagani sabab
        #     har transfer va har item uchun alohida SQL chiqardi → 38k×N so'rov. Ikkalasi birga = sekinlik.
        #
        #   HOZIR:
        #     (1) `select_related("from_store", "to_store", "approved_by")` — do'kon/user FK'lari
        #         asosiy so'rovga JOIN bo'lib keladi (qo'shimcha so'rovsiz).
        #     (2) `prefetch_related("items__product")` — item'lar va ular product'i jami 2 ta
        #         qo'shimcha so'rov bilan olinadi (item bo'yicha N+1 yo'q).
        #     (3) `StandardPagination` — javob har sahifada 20 qator (max 100), `?page=` / `?limit=`.
        #   Tartib model `Meta.ordering = ["-created_at"]` orqali saqlanadi — natija barqaror sahifalanadi.
        #   Model/serializer/tartib o'zgarmadi — faqat javob sahifalangan va so'rovlar kamaydi.
        #
        # ⓘ StandardPagination (PageNumberPagination) bu ro'yxat uchun yetarli: 38k qatorda COUNT va
        #   LIMIT tez ishlaydi. Agar kelajakda juda chuqur sahifalash (katta `?page=`) kerak bo'lsa,
        #   OFFSET sekinlashadi — o'shanda `created_at` bo'yicha CursorPagination'ga o'tish tavsiya etiladi.
        transfers = (
            StockTransfer.objects
            .select_related("from_store", "to_store", "approved_by")
            .prefetch_related("items__product")
        )

        # 🔍 QIDIRUV: `?search=` — o'tkazma ID, mahsulot ID/nomi/SKU/shtrixkod va do'kon nomlari bo'yicha.
        # `items` JOIN'i bir o'tkazmani bir necha qator qilib qaytarishi mumkin, shuning uchun `.distinct()`.
        search = (request.query_params.get("search") or "").strip()
        if search:
            query = (
                Q(items__product__name__icontains=search)
                | Q(items__product__sku__icontains=search)
                | Q(items__product__barcode__icontains=search)
                | Q(from_store__name__icontains=search)
                | Q(to_store__name__icontains=search)
            )
            if search.isdigit():
                query |= Q(id=int(search)) | Q(items__product__id=int(search))
            transfers = transfers.filter(query).distinct()

        # DIQQAT: bitta paginator instance ishlatiladi — `get_paginated_response` `paginate_queryset`
        # o'rnatgan `self.page` holatiga tayanadi, shu sabab ikkalasi ayni instance'da chaqiriladi.
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(transfers, request, view=self)
        serializer = self.serializer_class(page, many=True)
        return paginator.get_paginated_response(serializer.data)


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
# 📊 FAYL XULOSASI (yangilangan)
# Kritik muammolar soni: 1  (avval 2 edi — TransferListAPIView pagination+N+1 TUZATILDI)
# Performance muammolari: 0  (avval 1 edi — TUZATILDI)
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10  (avval 6/10)
# ✅ BAJARILDI: TransferListAPIView endi StandardPagination + select_related/prefetch_related bilan ishlaydi
#   — "juda uzoq loading" muammosi hal qilindi.
# Prioritet bo'yicha birinchi hal qilinishi kerak: [NotificationListAPIView'ga permission_classes qo'shish]
# ═══════════════════════════════
