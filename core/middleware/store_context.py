# core/middleware/store_context.py

from django.utils.deprecation import MiddlewareMixin
from apps.store.models import StoreUser


class StoreContextMiddleware(MiddlewareMixin):

    def process_request(self, request):
        request.store = None
        request.store_user = None

        if not request.user.is_authenticated:
            return

        store_id = request.headers.get("X-Store-ID")

        if not store_id:
            return

        store_user = (
            StoreUser.objects
            .select_related("store")
            .filter(
                user=request.user,
                store_id=store_id,
                is_active=True
            )
            .first()
        )

        if store_user:
            request.store = store_user.store
            request.store_user = store_user