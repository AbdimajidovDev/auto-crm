from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.common.i18n import tr
from apps.store.models import StoreUser
from apps.store.repositories import StoreUserRepository
from apps.store.selectors import StoreSelector
from apps.users.models import Role
from apps.users.permissions import user_has_perm
from apps.users.repositories import UserRepository
from apps.users.selectors import UserSelector

class UserService:

    @staticmethod
    @transaction.atomic
    def create_seller_with_store(*, request_user, data: dict):

        store_id = data.pop("store_id", None)
        role = data.pop("role", None)
        role_id = data.pop("role_id", None)

        # 🔴 AUTH CHECK (bitta joyda bo‘lishi kerak)
        # superuser yoki "users.create" permission'iga ega rol
        if not user_has_perm(request_user, "users.create"):
            raise ValidationError(tr("no_permission_user_create"))

        # 🔴 DUPLICATE USER
        if UserSelector.get_user_by_phone(data["phone_number"]):
            raise ValidationError(tr("phone_exists"))

        # 🔴 STORE CHECK (store ixtiyoriy — admin turidagi user do'konsiz bo'lishi mumkin)
        store = None
        if store_id:
            store = StoreSelector.get_store(store_id)
            if not store:
                raise ValidationError(tr("store_not_found"))

        # Tizim roli (RBAC)
        role_obj = None
        if role_id is not None:
            role_obj = Role.objects.filter(pk=role_id).first()
            data["role"] = role_obj

        # ✅ USER CREATE
        user = UserRepository.create_user(**data)

        # 🔴 ATTACH
        if store:
            # Statik do'kon roli (m/s) endi formada so'ralmaydi — tizim rolidan
            # kelib chiqadi: inventarizatsiya huquqi bor rol 'm', aks holda 's'.
            store_role = role or "s"
            if role_obj is not None:
                store_role = "m" if "inventory.view" in (role_obj.permissions or []) else "s"
            StoreUserRepository.create_store_user(
                user=user,
                store=store,
                role=store_role,
            )

        return user

    @staticmethod
    @transaction.atomic
    def set_user_store(*, user, store_id):
        # Tahrirlashda userning aktiv do'kon bog'lamasini sinxronlaydi.
        # store_id bo'sh/null bo'lsa — barcha aktiv bog'lamalar uziladi (admin turidagi user).
        if not store_id:
            StoreUser.objects.filter(user=user, is_active=True).update(is_active=False)
            return

        store = StoreSelector.get_store(store_id)
        if not store:
            raise ValidationError(tr("store_not_found"))

        # Statik do'kon roli (m/s) tizim rolidan kelib chiqadi — create bilan bir xil qoida
        store_role = "m" if user.role and "inventory.view" in (user.role.permissions or []) else "s"

        # Boshqa do'konlardagi aktiv bog'lamalarni uzamiz
        StoreUser.objects.filter(user=user, is_active=True).exclude(store=store).update(is_active=False)

        # unique_together(user, store) — mavjud bog'lama bo'lsa qayta aktivlashtiramiz
        link = StoreUser.objects.filter(user=user, store=store).first()
        if link:
            if not link.is_active or link.role != store_role:
                link.is_active = True
                link.role = store_role
                link.save()
        else:
            StoreUserRepository.create_store_user(user=user, store=store, role=store_role)


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