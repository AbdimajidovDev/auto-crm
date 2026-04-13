from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.store.repositories import StoreUserRepository
from apps.store.selectors import StoreSelector
from apps.users.repositories import UserRepository
from apps.users.selectors import UserSelector

class UserService:

    @staticmethod
    @transaction.atomic
    def create_seller_with_store(*, request_user, data: dict):

        store_id = data.pop("store_id")

        # 🔴 AUTH CHECK (bitta joyda bo‘lishi kerak)
        if not request_user.is_superuser:
            raise ValidationError("Faqat superuser seller yaratishi mumkin")

        # 🔴 DUPLICATE USER
        if UserSelector.get_user_by_phone(data["phone_number"]):
            raise ValidationError("User already exists")

        # 🔴 STORE CHECK
        store = StoreSelector.get_store(store_id)
        if not store:
            raise ValidationError("Store topilmadi")

        # ✅ USER CREATE
        user = UserRepository.create_user(**data)

        # 🔴 ATTACH
        StoreUserRepository.create_store_user(
            user=user,
            store=store
        )

        return user


    @staticmethod
    @transaction.atomic
    def create_user(data: dict):
        # validation (business level)
        if UserSelector.get_user_by_phone(data["phone_number"]):
            raise ValueError("User already exists")

        return UserRepository.create_user(**data)

    @staticmethod
    @transaction.atomic
    def update_user(user_id: int, data: dict):
        user = UserSelector.get_user_by_id(user_id)

        if not user:
            raise ValueError("User not found")

        return UserRepository.update_user(user, **data)

    @staticmethod
    @transaction.atomic
    def delete_user(user_id: int):
        user = UserSelector.get_user_by_id(user_id)

        if not user:
            raise ValueError("User not found")

        UserRepository.delete_user(user)