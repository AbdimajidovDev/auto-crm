from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.store.models import StoreUser
from apps.store.repositories import StoreUserRepository
from apps.store.selectors import StoreSelector, UserSelector


class StoreUserService:

    @staticmethod
    @transaction.atomic
    def attach_user(*, request_user, user_id: int, store_id: int):

        # 🔴 AUTH CHECK
        if not request_user.is_superuser:
            raise ValidationError("Faqat superuser biriktira oladi")

        # 🔴 USER CHECK
        user = UserSelector.get_user(user_id)
        if not user:
            raise ValidationError("User topilmadi")

        # 🔴 STORE CHECK
        store = StoreSelector.get_store(store_id)
        if not store:
            raise ValidationError("Store topilmadi")

        # 🔴 SELF PROTECTION
        if user.id == request_user.id:
            raise ValidationError("O‘zingizni biriktira olmaysiz")

        # 🔴 DUPLICATE CHECK
        if StoreUser.objects.filter(user=user, store=store).exists():
            raise ValidationError("User allaqachon ushbu storega biriktirilgan")

        return StoreUserRepository.create_store_user(user=user, store=store)