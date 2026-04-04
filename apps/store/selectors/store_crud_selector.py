from apps.store.models import StoreUser, Store


class StoreSelector:

    @staticmethod
    def user_has_store(user):
        return StoreUser.objects.filter(user=user, is_active=True).exists()

    @staticmethod
    def store_list():
        return Store.objects.all()

    @staticmethod
    def get_store(store_id: int):
        return Store.objects.filter(id=store_id, is_active=True).first()

