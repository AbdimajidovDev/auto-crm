from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.store.repositories import StoreRepository


class StoreService:

    @staticmethod
    @transaction.atomic
    def create_store(*, user, data: dict):

        # 🔴 AUTH
        if not user.is_authenticated:
            raise ValidationError("Foydalanuvchi autentifikatsiyadan o'tmagan")

        # 🔴 ONLY SUPERUSER
        if not user.is_superuser:
            raise ValidationError("Faqat tizim egasi do‘kon yarata oladi")

        # 🔴 CREATE STORE
        store = StoreRepository.create_store(**data)

        return store