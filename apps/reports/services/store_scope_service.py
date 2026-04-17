# services/store_scope.py

from apps.store.models import StoreUser


class StoreScopeService:

    @staticmethod
    def get_user_stores(user):
        if user.is_superuser:
            return None  # barcha store

        return StoreUser.objects.filter(
            user=user,
            is_active=True
        ).values_list("store_id", flat=True)



class ReportStoreScope:

    @staticmethod
    def resolve(store_id):
        if store_id and store_id != "all":
            return [int(store_id)]
        return None  # all stores
