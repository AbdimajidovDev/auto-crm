from django.db import IntegrityError
from apps.store.models import Store, StoreUser


class StoreRepository:

    @staticmethod
    def create_store(**data) -> Store:
        try:
            return Store.objects.create(**data)
        except IntegrityError as e:
            raise ValueError("Store yaratishda xatolik") from e


class StoreUserRepository:

    @staticmethod
    def create_store_user(user, store):
        try:
            return StoreUser.objects.create(
                user=user,
                store=store,
                role=StoreUser.Role.SELLER
            )
        except IntegrityError:
            raise ValueError("User allaqachon ushbu storega biriktirilgan")