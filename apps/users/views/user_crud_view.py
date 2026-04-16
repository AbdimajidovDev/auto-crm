# users/views.py
from drf_spectacular.utils import extend_schema
from rest_framework.generics import ListAPIView, get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from apps.store.models import StoreUser
from apps.users.models import User
from apps.users.services import UserService
from apps.users.serializers import SellerCreateSerializer, UserResponseSerializer
from apps.users.selectors import UserSelector
from apps.users.serializers import UserSerializer


@extend_schema(
    tags=['User'],
    summary="- Seller yaratish uchun API",
)
class UsersListView(ListAPIView):
    queryset = UserSelector.list_users()
    serializer_class = UserSerializer
    permission_classes = (permissions.AllowAny,)
    pagination_class = None

    def get_queryset(self):
        from django.db.models import Prefetch

        queryset = User.objects.prefetch_related(
            Prefetch(
                "store_links",
                queryset=StoreUser.objects.filter(is_active=True).select_related("store"),
                to_attr="active_store_links"
            )
        )
        return queryset




@extend_schema(
    tags=['User'],
    summary="- ID orqali bitta Userni ko'rish uchun API.",
)
class UsersDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = UserSerializer

    def get(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        serializer = self.serializer_class(user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        user = UserSelector.get_user_by_id(user_id=pk)
        serializer = self.serializer_class(user, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        user = UserSelector.get_user_by_id(user_id=pk)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



@extend_schema(
    tags=['User'],
    summary="- Seller yaratish uchun API",
)
class SellerCreateAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_classes = SellerCreateSerializer

    def post(self, request):
        serializer = self.serializer_classes(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user = UserService.create_seller_with_store(
                request_user=request.user,
                data=serializer.validated_data
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)

        return Response(
            UserResponseSerializer(user).data,
            status=status.HTTP_201_CREATED
        )
