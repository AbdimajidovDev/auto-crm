from django.contrib.auth.models import PermissionsMixin
from rest_framework.permissions import BasePermission


class IsStoreMember(BasePermission):
    def has_permission(self, request, view):
        return request.store_user is not None


class IsSuperUser(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_superuser


class IsSeller(BasePermission):
    def has_permission(self, request, view):
        user = request.store_user
        return (
            user
            and user.is_authenticated
            and user.role in ["m", "s"]
        )
