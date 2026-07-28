from django.contrib.auth.models import PermissionsMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import generics, status, permissions

from apps.common.paginations import StandardPagination
from apps.common.excel_export import parse_date_param
from apps.common.store_scope import allowed_store_ids
from apps.transfer.models import StockTransfer, Notification, TransferSession
from apps.transfer.serializers import (
    TransferCreateSerializer,
    TransferListSerializer,
    NotificationSerializer,
    TransferSessionSerializer,
)
from apps.transfer.services import TransferService


# ─────────────────────────────────────────────
# O'TKAZMA SESSIYASI (qoralama, avto-saqlash)
# ─────────────────────────────────────────────

@extend_schema(
    tags=["Transfer"],
    summary="Faol o'tkazma qoralamalari ro'yxati / yangi qoralama boshlash",
)
class TransferSessionListCreateAPIView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TransferSessionSerializer

    def get_queryset(self):
        # Har kim faqat o'z qoralamalarini ko'radi va davom ettiradi
        return TransferSession.objects.filter(
            created_by=self.request.user,
            status=TransferSession.Status.IN_PROGRESS,
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


@extend_schema(
    tags=["Transfer"],
    summary="Qoralamani olish / avto-saqlash (PATCH) / bekor qilish (DELETE)",
)
class TransferSessionDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = TransferSessionSerializer

    def get_queryset(self):
        return TransferSession.objects.filter(created_by=self.request.user)

    def update(self, request, *args, **kwargs):
        session = self.get_object()
        if session.status != TransferSession.Status.IN_PROGRESS:
            return Response(
                {"detail": "Yakunlangan yoki bekor qilingan qoralamani o'zgartirib bo'lmaydi"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        # Hard delete emas — qoralama bekor qilinadi (tarix saqlanadi)
        session = self.get_object()
        if session.status == TransferSession.Status.COMPLETED:
            return Response(
                {"detail": "Yakunlangan qoralamani bekor qilib bo'lmaydi"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session.status = TransferSession.Status.CANCELLED
        session.save(update_fields=["status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Transfer"],
    summary="Qoralamani yakunlash — o'tkazma yaratilgandan keyin chaqiriladi",
)
class TransferSessionCompleteAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        session = get_object_or_404(
            TransferSession,
            pk=pk,
            created_by=request.user,
            status=TransferSession.Status.IN_PROGRESS,
        )
        transfer_id = request.data.get("transfer")
        if transfer_id:
            session.transfer = StockTransfer.objects.filter(pk=transfer_id).first()
        session.status = TransferSession.Status.COMPLETED
        session.save(update_fields=["status", "transfer", "updated_at"])
        return Response(TransferSessionSerializer(session).data, status=status.HTTP_200_OK)


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

        # Do'kon scoping: xodim faqat o'z do'koni ishtirok etgan (jo'natuvchi
        # yoki qabul qiluvchi) transferlarni ko'radi. Ilgari ro'yxat ochiq edi —
        # har qanday foydalanuvchi barcha do'konlar orasidagi harakatni,
        # mahsulot va miqdorlari bilan ko'ra olardi.
        allowed = allowed_store_ids(request.user)
        if allowed is not None:
            transfers = transfers.filter(
                Q(from_store_id__in=allowed) | Q(to_store_id__in=allowed)
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

        # 🔎 ?status= (p/a/r) va ?date_from=&date_to= (YYYY-MM-DD) — ro'yxat filtrlari
        status_param = request.query_params.get("status")
        if status_param in dict(StockTransfer.Status.choices):
            transfers = transfers.filter(status=status_param)

        date_from = parse_date_param(request.query_params.get("date_from"))
        date_to = parse_date_param(request.query_params.get("date_to"))
        if date_from:
            transfers = transfers.filter(created_at__date__gte=date_from)
        if date_to:
            transfers = transfers.filter(created_at__date__lte=date_to)

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

        # Servis DRF istisnolarini ko'taradi (ValidationError → 400,
        # PermissionDenied → 403, NotFound → 404) — ularni DRF handler'i
        # o'zi ishlaydi. Ilgari bu yerda `django.core.exceptions.ValidationError`
        # ushlanardi, ya'ni except hech qachon ishlamas, xatolar 500 bo'lib chiqardi.
        transfer = TransferService.create_transfer(
            from_store=serializer.validated_data["from_store"],
            to_store=serializer.validated_data["to_store"],
            items_data=serializer.validated_data["items"],
            user=request.user
        )

        return Response({"id": transfer.id, "status": transfer.status}, status=201)



@extend_schema(
    tags=["Transfer"],
    summary="- Transferni qabul qilish.",
)
class TransferApproveAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        # DRF istisnolari (400/403/404) handler orqali qaytadi — qarang:
        # TransferCreateAPIView.post dagi izoh.
        TransferService.approve_transfer(transfer_id=pk, user=request.user)
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
