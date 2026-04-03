# users/services.py

from django.db import transaction
from apps.users.repositories import UserRepository
from apps.users.selectors import UserSelector


class UserService:

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