from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.generics import RetrieveAPIView

from apps.store.models import Store, StoreUser
from apps.users.models import User
from apps.users.serializers.profile_serializer import ProfileSerializer


# ─────────────────────────────────────────────
#  View
# ─────────────────────────────────────────────
@extend_schema(tags=["Profile"])
class ProfileView(RetrieveAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = ProfileSerializer

    def get_object(self):
        from django.db.models import CharField, OuterRef, Prefetch, Subquery
        from apps.users.models import UserHistory

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

        # ── history prefetch — bitta SQL ────────────────────
        user_with_history = (
            User.objects
            .prefetch_related(
                Prefetch(
                    "history",
                    queryset=UserHistory.objects.order_by("-created_at"),
                )
            )
            .get(pk=user.pk)
        )

        # stores ni attribute sifatida beramiz — serializer source="prefetched_stores"
        user_with_history.prefetched_stores = stores

        return user_with_history
