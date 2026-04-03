# users/selectors.py

from apps.users.models import User


class UserSelector:

    @staticmethod
    def get_user_by_id(user_id: int) -> User:
        return User.objects.filter(id=user_id).only(
            "id", "phone_number", "full_name", "role"
        ).first()

    @staticmethod
    def get_user_by_phone(phone: str) -> User:
        return User.objects.filter(phone_number=phone).first()

    @staticmethod
    def list_users():
        return User.objects.all().only(
            "id", "role", "full_name", "phone_number", "email",
            "is_active", "created_at", "updated_at"
        )