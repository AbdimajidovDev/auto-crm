from django.db.models import Count
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models import Role
from apps.users.permissions import catalog_for_api
from apps.users.serializers.role_serializer import RoleSerializer


def _roles_queryset():
    return Role.objects.annotate(users_count=Count("users")).order_by("name")


@extend_schema(
    tags=["Role"],
    summary="- Rollar ro'yxati / yangi rol yaratish.",
)
class RoleListCreateAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = RoleSerializer

    def get(self, request):
        serializer = self.serializer_class(_roles_queryset(), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.save()
        role.users_count = 0
        return Response(self.serializer_class(role).data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["Role"],
    summary="- Rolni ko'rish / tahrirlash / o'chirish.",
)
class RoleDetailAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = RoleSerializer

    def get(self, request, pk):
        role = get_object_or_404(_roles_queryset(), pk=pk)
        return Response(self.serializer_class(role).data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        serializer = self.serializer_class(role, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        role = serializer.save()
        role.users_count = role.users.count()
        return Response(self.serializer_class(role).data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        attached = role.users.count()
        if attached:
            return Response(
                {
                    "detail": (
                        f"Bu rol {attached} ta foydalanuvchiga biriktirilgan. "
                        "Avval ularni boshqa rolga o'tkazing yoki rolni bo'shating."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        role.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Role"],
    summary="- Permission katalogi (modul va amallar ro'yxati).",
)
class PermissionCatalogAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        return Response(catalog_for_api(), status=status.HTTP_200_OK)
