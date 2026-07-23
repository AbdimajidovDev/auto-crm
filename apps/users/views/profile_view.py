from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import ListAPIView, RetrieveAPIView

from apps.common.paginations import StandardPagination
from apps.store.models import Store, StoreUser
from apps.users.models import User, UserHistory
from apps.users.serializers.profile_serializer import (
    ProfileSerializer,
    UserHistorySerializer,
)


# ─────────────────────────────────────────────
#  View
# ─────────────────────────────────────────────
@extend_schema(tags=["Profile"])
class ProfileView(RetrieveAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = ProfileSerializer

    def get_object(self):
        from django.db.models import CharField, OuterRef, Subquery

        user = self.request.user

        # ── stores ──────────────────────────────────────────
        if user.is_superuser:
            stores = (
                Store.objects
                .filter(is_active=True)
                .order_by("name")
            )
        else:
            role_subquery = (
                StoreUser.objects
                .filter(user=user, store=OuterRef("pk"), is_active=True)
                .values("role")[:1]
            )
            stores = (
                Store.objects
                .filter(
                    user_links__user=user,
                    user_links__is_active=True,
                    is_active=True,
                )
                .annotate(
                    role_in_store=Subquery(role_subquery, output_field=CharField())
                )
                .distinct()
                .order_by("name")
            )

        # ── history — DB darajasida oxirgi 5 qator (LIMIT) ──
        # ✅ BAJARILDI: avvalgi `Prefetch("history", ...)` foydalanuvchining BARCHA
        # history qatorlarini yuklab, serializer Pythonda sort+slice qilardi.
        # Endi faqat kerakli 5 qator DB'dan olinadi; to'liq ro'yxat alohida
        # sahifalangan endpoint'da (LoginHistoryListAPIView) beriladi.
        user_with_history = (
            User.objects
            .select_related("role")
            .get(pk=user.pk)
        )
        user_with_history.recent_history = list(
            UserHistory.objects.filter(user=user).order_by("-created_at")[:5]
        )

        # stores ni attribute sifatida beramiz — serializer source="prefetched_stores"
        user_with_history.prefetched_stores = stores

        return user_with_history


@extend_schema(
    tags=["Profile"],
    summary="Kirishlar tarixi — joriy foydalanuvchining login/logout yozuvlari (sahifalangan)",
)
class LoginHistoryListAPIView(ListAPIView):
    """
    Sozlamalar sahifasidagi "Kirishlar tarixi" ro'yxati.
    StandardPagination: ?page= / ?limit= (max 100).
    60 kundan eski yozuvlar ko'rsatilmaydi — ular kunlik prune (audit
    middleware) bilan o'chiriladi; prune hali ishlamagan bo'lsa ham
    cutoff filtri ularni yashiradi.
    """

    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = UserHistorySerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        return (
            UserHistory.objects
            .filter(
                user=self.request.user,
                created_at__gte=UserHistory.retention_cutoff(),
            )
            .order_by("-created_at")
        )


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [history prefetch'ini oxirgi 5 qator bilan cheklash — o'sib boruvchi UserHistory jadvalidan butun tarixni yuklamaslik]
# ═══════════════════════════════
