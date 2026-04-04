from django.db import IntegrityError
from apps.store.models import StoreUser


class StoreUserRepository:

    @staticmethod
    def create(user, store):
        try:
            return StoreUser.objects.create(
                user=user,
                store=store,
                role=StoreUser.Role.SELLER
            )
        except IntegrityError:
            raise ValueError("User allaqachon storega biriktirilgan")