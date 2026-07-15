from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contract.models import PurchaseSession
from apps.contract.permissions import CanCreateStockEntry, ensure_store_access
from apps.contract.serializers.purchase_session_serializer import PurchaseSessionSerializer
from apps.contract.serializers.stock_entry_serializer import StockEntryCreateSerializer
from apps.contract.services import StockEntryService


ACTIVE_STATUSES = (
    PurchaseSession.Status.IN_PROGRESS,
    PurchaseSession.Status.RECEIVED,
)


def _session_items_payload(session: PurchaseSession) -> list[dict]:
    """Sessiya JSON itemlarini StockEntryCreateSerializer formatiga o'tkazadi."""
    payload = []
    for item in (session.items or []):
        try:
            quantity = int(Decimal(str(item.get("quantity") or 0)))
        except (InvalidOperation, TypeError, ValueError):
            quantity = 0
        payload.append({
            "product": item.get("product"),
            "quantity": quantity,
            "purchase_price": str(item.get("purchase_price") or 0),
            "selling_price": str(item.get("selling_price") or 0),
            "wholesale_price": str(item.get("wholesale_price") or 0),
        })
    return payload


@extend_schema(
    tags=["Purchase Session"],
    summary="Faol kirim sessiyalari ro'yxati / yangi sessiya boshlash",
)
class PurchaseSessionListCreateAPIView(generics.ListCreateAPIView):
    permission_classes = [CanCreateStockEntry]
    serializer_class = PurchaseSessionSerializer

    def get_queryset(self):
        # Har kim faqat o'z qoralamalarini ko'radi va davom ettiradi
        return (
            PurchaseSession.objects
            .filter(created_by=self.request.user, status__in=ACTIVE_STATUSES)
            .select_related("supplier", "store", "bank_card")
        )

    def perform_create(self, serializer):
        # Do'kon xodimi faqat o'z do'koniga sessiya ochadi (superuser — hammasiga)
        ensure_store_access(self.request.user, serializer.validated_data["store"].id)
        serializer.save(created_by=self.request.user)


@extend_schema(
    tags=["Purchase Session"],
    summary="Sessiyani olish / avto-saqlash (PATCH) / bekor qilish (DELETE)",
)
class PurchaseSessionDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [CanCreateStockEntry]
    serializer_class = PurchaseSessionSerializer

    def get_queryset(self):
        return (
            PurchaseSession.objects
            .filter(created_by=self.request.user)
            .select_related("supplier", "store", "bank_card")
        )

    def update(self, request, *args, **kwargs):
        session = self.get_object()
        if session.status not in ACTIVE_STATUSES:
            return Response(
                {"detail": "Yakunlangan yoki bekor qilingan sessiyani o'zgartirib bo'lmaydi"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Avto-saqlashda do'kon o'zgartirilsa ham faqat ruxsat etilgan do'kon bo'lishi kerak
        new_store = request.data.get("store")
        if new_store is not None:
            ensure_store_access(request.user, new_store)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        # Hard delete emas — sessiya bekor qilinadi (tarix saqlanadi)
        session = self.get_object()
        if session.status == PurchaseSession.Status.COMPLETED:
            return Response(
                {"detail": "Tasdiqlangan sessiyani bekor qilib bo'lmaydi"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session.status = PurchaseSession.Status.CANCELLED
        session.save(update_fields=["status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Purchase Session"],
    summary="Mahsulotlarni qabul qilish — sessiya 'qabul qilingan (tasdiqlanmagan)' holatga o'tadi",
)
class PurchaseSessionReceiveAPIView(APIView):
    """
    2-bosqich yakuni: mahsulotlar ro'yxati to'liq validatsiyadan o'tadi va
    sessiya RECEIVED holatiga o'tadi. Ombor hali O'ZGARMAYDI — bu faqat
    "tovar qabul qilindi, tasdiqlash to'lovdan keyin" belgisi.
    """
    permission_classes = [CanCreateStockEntry]

    def post(self, request, pk):
        session = get_object_or_404(
            PurchaseSession,
            pk=pk,
            created_by=request.user,
            status__in=ACTIVE_STATUSES,
        )

        # Itemlar va supplier/store haqiqiy kirim talablariga mos kelishini
        # oldindan tekshiramiz (to'lovsiz — cash/card 0 bilan ham o'tadi)
        probe = StockEntryCreateSerializer(data={
            "supplier": session.supplier_id,
            "store": session.store_id,
            "cash_amount": "0",
            "card_amount": "0",
            "items": _session_items_payload(session),
        })
        probe.is_valid(raise_exception=True)

        session.status = PurchaseSession.Status.RECEIVED
        session.current_step = 3
        session.save(update_fields=["status", "current_step", "updated_at"])

        return Response(PurchaseSessionSerializer(session).data, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Purchase Session"],
    summary="Sessiyani tasdiqlash — haqiqiy kirim (StockEntry) yaratiladi",
)
class PurchaseSessionConfirmAPIView(APIView):
    """
    3-bosqich yakuni: faqat RECEIVED sessiya tasdiqlanadi.
    To'lov ma'lumotlari bilan birga StockEntryCreateSerializer orqali to'liq
    validatsiya qilinadi va StockEntryService.create_entry chaqiriladi
    (ombor partiyalari, ta'minotchi qarzi — hammasi shu yerda o'zgaradi).
    """
    permission_classes = [CanCreateStockEntry]

    @transaction.atomic
    def post(self, request, pk):
        session = get_object_or_404(
            PurchaseSession.objects.select_for_update(),
            pk=pk,
            created_by=request.user,
        )

        if session.status != PurchaseSession.Status.RECEIVED:
            return Response(
                {"detail": "Avval mahsulotlar qabul qilinishi kerak — tasdiqlash faqat to'lov bosqichida mumkin"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Yakuniy himoya: ombor o'zgarishidan oldin do'kon ruxsati qayta tekshiriladi
        ensure_store_access(request.user, session.store_id)

        serializer = StockEntryCreateSerializer(data={
            "supplier": session.supplier_id,
            "store": session.store_id,
            "cash_amount": str(session.cash_amount),
            "card_amount": str(session.card_amount),
            "bank_card": session.bank_card_id,
            "note": session.note or "",
            "items": _session_items_payload(session),
        })
        serializer.is_valid(raise_exception=True)

        entry = StockEntryService.create_entry(
            supplier=serializer.validated_data["supplier"],
            store=serializer.validated_data["store"],
            cash_amount=serializer.validated_data["cash_amount"],
            card_amount=serializer.validated_data["card_amount"],
            bank_card=serializer.validated_data.get("bank_card"),
            note=serializer.validated_data.get("note", ""),
            items=serializer.validated_data["items"],
            user=request.user,
        )

        session.status = PurchaseSession.Status.COMPLETED
        session.entry = entry
        session.save(update_fields=["status", "entry", "updated_at"])

        return Response({
            "status": "success",
            "id": entry.id,
            "session_id": session.id,
            "items_count": len(serializer.validated_data["items"]),
            "payment_type": entry.payment_type,
            "paid_amount": entry.paid_amount,
            "debt_amount": entry.debt_amount,
        }, status=status.HTTP_201_CREATED)
