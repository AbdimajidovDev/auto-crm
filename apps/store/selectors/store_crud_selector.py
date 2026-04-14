from rest_framework.generics import get_object_or_404

from apps.store.models import StoreUser, Store


class StoreSelector:

    @staticmethod
    def get_store(pk):
        return get_object_or_404(Store, pk=pk)

    @staticmethod
    def user_has_store(user):
        return StoreUser.objects.filter(user=user, is_active=True).exists()

    @staticmethod
    def store_list():
        return Store.objects.all()

