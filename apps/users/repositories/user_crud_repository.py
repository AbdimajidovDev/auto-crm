# users/repositories.py
from apps.users.models.user import User


class UserRepository:

    @staticmethod
    def create_user(**data) -> User:
        return User.objects.create_user(**data)

    @staticmethod
    def update_user(user: User, **data) -> User:
        for field, value in data.items():
            setattr(user, field, value)
        user.save(update_fields=data.keys())
        return user

    @staticmethod
    def delete_user(user: User):
        user.delete()