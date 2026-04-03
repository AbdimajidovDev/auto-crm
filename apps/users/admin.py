from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):

    ordering = ("-created_at",)
    list_display = (
        "id", "full_name", "phone_number", "email", "role", "is_active",
    )
    list_filter = (
        "role", "is_staff", "is_superuser", "is_active", "created_at",
    )
    search_fields = (
        "phone_number", "email", "full_name",
    )

    readonly_fields = ("id", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("phone_number", "password")}),
        (_("Personal info"), {
            "fields": ("full_name", "email")
        }),
        (_("Role & Permissions"), {
            "fields": ("role", "is_active", "is_staff", "is_superuser")
        }),
        (_("Important dates"), {
            "fields": ("last_login", "created_at", "updated_at")
        }),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "full_name", "phone_number", "email", "role", "password1", "password2",
                "is_staff", "is_superuser", "is_active",
            ),
        }),
    )




# @admin.register(VerificationOTP)
# class VerificationOTPAdmin(admin.ModelAdmin):
#     list_display = ('id', 'user', 'code', 'expires_at', 'is_used')
