from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError as DjangoValidationError
from django.db import models, transaction
from rest_framework.exceptions import ValidationError

from apps.common.i18n import tr
from apps.store.models import StoreUser
from apps.store.repositories import StoreUserRepository
from apps.store.selectors import StoreSelector
from apps.users.models import Role, User
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
    def delete_user(user_id: int, requesting_user=None):
        """
        Safely delete a user record while enforcing administrative invariants:
        1. Target user must exist.
        2. Permission check: requesting_user must have 'users.delete' or be a superuser.
        3. Self-deletion protection: requesting_user cannot delete their own account.
        4. Superuser protection:
           - Non-superusers cannot delete a superuser.
           - Superusers cannot delete the last active superuser.
        5. Last active admin protection:
           - If target_user is an active admin (is_superuser, is_staff, or has 'users.delete'),
             at least one other active admin must remain in the system.
           - Concurrency / race condition protection: Locks active administrative users
             using deterministic ascending id order (select_for_update) to prevent 
             simultaneous deletion race conditions where two admins delete each other.
        6. Foreign Key protection:
           - Financial/sales records (Sale.seller, SaleReturn.seller) are protected by DB ON DELETE PROTECT.
        """
        # 1. Target user existence check
        target_user = User.objects.filter(pk=user_id).first()
        if not target_user:
            raise ObjectDoesNotExist("Foydalanuvchi topilmadi.")

        # 2. Permission check
        if requesting_user is not None:
            if not (requesting_user.is_superuser or user_has_perm(requesting_user, "users.delete")):
                raise PermissionDenied("Foydalanuvchilarni o'chirish uchun ruxsat berilmagan.")

        # 3. Self-deletion check
        if requesting_user is not None and requesting_user.pk == target_user.pk:
            raise DjangoValidationError("Foydalanuvchi o'z hisobini o'zi o'chira olmaydi.")

        # 4. Superuser protection
        if target_user.is_superuser:
            if requesting_user is not None and not requesting_user.is_superuser:
                raise PermissionDenied("Superuser hisobini faqat superuser o'chira oladi.")

            active_superusers = list(
                User.objects.filter(is_superuser=True, is_active=True).order_by("id").select_for_update()
            )
            remaining_superusers = [u for u in active_superusers if u.id != target_user.id]
            if not remaining_superusers:
                raise DjangoValidationError("Tizimdagi oxirgi faol superuserni o'chirib bo'lmaydi.")

        # 5. Last active admin protection
        base_admins = set(
            User.objects.filter(is_active=True).filter(
                models.Q(is_superuser=True) | models.Q(is_staff=True)
            ).values_list("id", flat=True)
        )
        roles = Role.objects.all()
        admin_role_ids = [r.id for r in roles if r.permissions and "users.delete" in r.permissions]
        if admin_role_ids:
            role_admins = User.objects.filter(
                is_active=True, role_id__in=admin_role_ids
            ).values_list("id", flat=True)
            base_admins.update(role_admins)

        active_admin_ids = sorted(list(base_admins))

        if target_user.id in active_admin_ids:
            locked_admins = list(
                User.objects.filter(id__in=active_admin_ids).order_by("id").select_for_update()
            )
            remaining_active_admins = [
                u for u in locked_admins if u.id != target_user.id and u.is_active
            ]
            if not remaining_active_admins:
                raise DjangoValidationError("Tizimdagi oxirgi faol administratorni o'chirib bo'lmaydi.")

        # 6. Ensure target user row is locked
        locked_target = User.objects.filter(pk=user_id).select_for_update().first()
        if not locked_target:
            raise ObjectDoesNotExist("Foydalanuvchi topilmadi.")

        # 7. Perform deletion via repository
        UserRepository.delete_user(locked_target)