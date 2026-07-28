# users/serializers.py
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from apps.common.i18n import tr, tr_lazy, translate_password_errors
from apps.store.models import Store, StoreUser
from apps.users.models import Role, User
from apps.users.validations import check_valid_phone
from django.core.exceptions import ValidationError as DjangoValidationError


class UserSerializer(serializers.ModelSerializer):
    store_id = serializers.SerializerMethodField()
    store_name = serializers.SerializerMethodField()
    # unique xatolari DRF'ning inglizcha standart xabari o'rniga tanlangan tilda
    # qaytishi uchun maydonlar UniqueValidator bilan oshkora e'lon qilingan
    # (UniqueValidator update'da joriy instance'ni o'zi chetlab o'tadi)
    phone_number = serializers.CharField(
        error_messages={"required": tr_lazy("field_required"), "blank": tr_lazy("field_required")},
        validators=[UniqueValidator(queryset=User.objects.all(), message=tr_lazy("phone_exists"))],
    )
    email = serializers.EmailField(
        required=False,
        allow_null=True,
        allow_blank=True,
        error_messages={"invalid": tr_lazy("invalid_email")},
        validators=[UniqueValidator(queryset=User.objects.all(), message=tr_lazy("email_exists"))],
    )
    full_name = serializers.CharField(
        error_messages={"required": tr_lazy("field_required"), "blank": tr_lazy("field_required")},
    )
    # Tizim roli (RBAC): yozishda role_id, o'qishda role_id + role_name
    role_id = serializers.PrimaryKeyRelatedField(
        source="role",
        queryset=Role.objects.all(),
        required=False,
        allow_null=True,
        error_messages={"does_not_exist": tr_lazy("role_not_found"), "incorrect_type": tr_lazy("role_not_found")},
    )
    role_name = serializers.CharField(source="role.name", read_only=True, default=None)

    class Meta:
        model = User
        fields = (
            "id", "full_name", "phone_number", "email",
            "is_active", "created_at", "updated_at", 'store_id', 'store_name',
            "role_id", "role_name",
        )

    def validate_phone_number(self, phone):
        check_valid_phone(phone)
        return phone

    # ✅ YAXSHI: `active_store_links` view'dagi Prefetch(to_attr=...) dan keladi va `store` select_related qilingan,
    # `hasattr` bilan himoyalangan — SerializerMethodField ichida qo'shimcha so'rov yo'q, N+1 xavfi yo'q.
    # ⚠️ MUAMMO [ARXITEKTURA]: Ammo `active_store_links` faqat UsersListView'da beriladi. Prefetchsiz kelgan
    # obyektlarda (masalan UsersDetailView.get) `hasattr` False bo'lib store_id/store_name jimgina None qaytadi.
    # ✅ YECHIM: har ikkala view (list va detail) uchun bir xil prefetch qo'llash (yagona get_queryset helper).
    def get_store_id(self, obj):
        if hasattr(obj, "active_store_links") and obj.active_store_links:
            return obj.active_store_links[0].store.id
        return None

    def get_store_name(self, obj):
        if hasattr(obj, "active_store_links") and obj.active_store_links:
            return obj.active_store_links[0].store.name
        return None



_REQUIRED = {"required": tr_lazy("field_required"), "blank": tr_lazy("field_required")}


class SellerCreateSerializer(serializers.Serializer):
    full_name = serializers.CharField(write_only=True, error_messages=_REQUIRED)
    phone_number = serializers.CharField(write_only=True, error_messages=_REQUIRED)
    email = serializers.EmailField(write_only=True, error_messages={**_REQUIRED, "invalid": tr_lazy("invalid_email")})
    password = serializers.CharField(write_only=True, error_messages=_REQUIRED)
    confirm_password = serializers.CharField(write_only=True, error_messages=_REQUIRED)
    # store_id ixtiyoriy: do'konga bog'lanmagan (admin turidagi) user ham yaratish mumkin
    store_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    role = serializers.ChoiceField(StoreUser.Role.choices, required=False, default=StoreUser.Role.SELLER)
    # Tizim roli (RBAC) — majburiy: rolsiz user hech qanday amal bajara olmaydi
    # (apps/users/permissions.py), shuning uchun uni bo'sh qoldirish xatoga olib keladi.
    role_id = serializers.IntegerField(write_only=True, allow_null=False, error_messages=_REQUIRED)

    def validate_role_id(self, value):
        if not Role.objects.filter(pk=value).exists():
            raise serializers.ValidationError(tr("role_not_found"))
        return value

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(translate_password_errors(e))
        return value

    def validate(self, data):
        phone_number = data.get('phone_number')
        password = data.get('password')
        confirm_password = data.get('confirm_password')

        check_valid_phone(phone_number)

        if password != confirm_password:
            raise serializers.ValidationError({"error": tr("passwords_mismatch")})

        data.pop("confirm_password")
        return data

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError({"error": tr("email_exists")})
        return value


class UserResponseSerializer(serializers.ModelSerializer):
    store = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "phone_number", "full_name", "store", "email"]

    def get_store(self, obj):
        # ✅ YAXSHI: UserResponseSerializer faqat SellerCreateAPIView'da (POST) bitta obyekt uchun ishlatiladi,
        # shu bois bu yerdagi bittalik `store_links` so'rovi N+1 emas.
        # ⚠️ MUAMMO [PERF]: Agar kelajakda bu serializer ro'yxatga qo'llansa — har obyektda alohida so'rov (N+1) bo'ladi.
        # ✅ YECHIM: ro'yxat holatida view'da `Prefetch("store_links", ..., to_attr="active_store_links")` qilib,
        #   bu yerda prefetch atributidan o'qish.
        store_link = (
            obj.store_links
            .filter(is_active=True)
            .select_related("store")
            .first()
        )

        if not store_link:
            return None

        return store_link.store.name


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0  (joriy ishlatilishda N+1 yo'q — prefetch/single-obyekt bilan himoyalangan)
# Arxitektura muammolari: 1  (UserSerializer store maydonlari prefetchga bog'liq, detail view'da bo'sh keladi)
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak:
#   [1] store prefetch'ini list va detail view'lar o'rtasida birxillashtirish
# ═══════════════════════════════