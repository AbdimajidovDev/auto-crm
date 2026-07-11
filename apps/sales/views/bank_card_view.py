from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import BasePermission, IsAuthenticated, SAFE_METHODS
from rest_framework.response import Response

from apps.sales.models import BankCard
from apps.sales.serializers.bank_card_serializer import BankCardSerializer


class IsSuperuserOrReadOnly(BasePermission):
    """O'qish — barcha xodimlar (kassada karta tanlash uchun), yozish — faqat superuser."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_superuser)


@extend_schema(
    tags=["Bank Cards"],
    summary="Bank kartalari ro'yxati / yangi karta yaratish",
)
class BankCardListCreateAPIView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated, IsSuperuserOrReadOnly]
    serializer_class = BankCardSerializer

    def get_queryset(self):
        qs = BankCard.objects.all()
        # Kassa (sotuv) ekrani uchun faqat faol kartalar: ?is_active=true
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("1", "true"))
        return qs


@extend_schema(
    tags=["Bank Cards"],
    summary="Bank kartasini olish / tahrirlash / o'chirish (soft)",
)
class BankCardDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated, IsSuperuserOrReadOnly]
    serializer_class = BankCardSerializer
    queryset = BankCard.objects.all()

    def destroy(self, request, *args, **kwargs):
        # Hard delete emas — eski to'lovlar kartaga PROTECT bilan bog'langan,
        # shuning uchun karta faqat o'chiriladi (is_active=False), tarix saqlanadi.
        card = self.get_object()
        card.is_active = False
        if card.is_default:
            card.is_default = False
        card.save(update_fields=["is_active", "is_default", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
