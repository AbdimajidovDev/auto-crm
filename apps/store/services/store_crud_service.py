from django.db import transaction
from django.core.exceptions import ValidationError

from apps.store.repositories import StoreRepository, StoreUserRepository
from apps.store.models import StoreUser


class StoreService:

    @staticmethod
    @transaction.atomic
    def create_store_with_owner(*, user, data: dict):

        # 🔴 BUSINESS VALIDATION
        if not user.is_authenticated:
            raise ValidationError("Foydalanuvchi autentifikatsiyadan o'tmagan")

        # 🔴 STORE CREATE
        store = StoreRepository.create_store(**data)

        # 🔴 OWNER ASSIGN
        StoreUserRepository.create_store_user(
            user=user,
            store=store,
            role=StoreUser.Role.OWNER
        )

        return store