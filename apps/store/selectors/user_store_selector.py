from apps.store.models import Store
from apps.users.models import User


class UserSelector:

    @staticmethod
    def get_user(user_id: int):
        return User.objects.filter(id=user_id, is_active=True).first()