from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.common.models.timestamp_mixin import TimestampMixin
from apps.users.models.managers import UserManager


# Create your models here.


class User(AbstractBaseUser, PermissionsMixin, TimestampMixin):

    class UserRole(models.TextChoices):
        SUPER_USER = "su", "Super User"
        SELLER = "s", "Seller"

    role = models.CharField(max_length=2, choices=UserRole.choices, default=UserRole.SELLER)
    full_name = models.CharField(max_length=128, null=True)
    phone_number = models.CharField(max_length=20, unique=True, db_index=True)
    email = models.EmailField(unique=True, db_index=True, blank=True, null=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # admin panel

    objects = UserManager()

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []

    def clean(self):
        super().clean()
        errors = {}

        if self.role  == self.UserRole.SUPER_USER:
            if not self.email:
                errors["email"] = "Super Admin uchun email majburiy."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.full_name} - ({self.phone_number})"

    def get_tokens(self):
        refresh = RefreshToken.for_user(self)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }
