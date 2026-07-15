from django.urls import path

from apps.users.views.customer_view import (
    CustomerListView,
    CustomerDetailView,
    CustomerCreateView,
)
from apps.users.views.profile_view import ProfileView
from apps.users.views.auth_view import (
    AdminLoginAPIView,
    LogOutView,
    ChangePasswordAPIView,
    ForgotPasswordView,
    ResetPasswordView,
    TokenRefreshAPIView,
)
from apps.users.views.user_crud_view import (
    SellerCreateAPIView,
    UsersListView,
    UsersDetailView,
)
from apps.users.views.role_view import (
    RoleListCreateAPIView,
    RoleDetailAPIView,
    PermissionCatalogAPIView,
)


urlpatterns = [

    # Profile
    path('profile/', ProfileView.as_view()),

    # Role (RBAC)
    path('roles/', RoleListCreateAPIView.as_view()),
    path('roles/catalog/', PermissionCatalogAPIView.as_view()),
    path('roles/<int:pk>/', RoleDetailAPIView.as_view()),

    # User part
    path('', UsersListView.as_view()),
    path('<int:pk>/', UsersDetailView.as_view()),

    # Seller part
    path('seller-create/', SellerCreateAPIView.as_view()),

    # Auth
    path('login/', AdminLoginAPIView.as_view()),
    path('logout/', LogOutView.as_view()),
    path("auth/refresh/", TokenRefreshAPIView.as_view(), name="token-refresh"),

    # Forgot password
    path('change-password/', ChangePasswordAPIView.as_view(), name='change-password'),
    path('auth/forgot-password/', ForgotPasswordView.as_view()),
    path('auth/reset-password/<uidb64>/<token>/', ResetPasswordView.as_view()),

    # Customer
    path('customers/list/', CustomerListView.as_view()),
    path('customers/create/', CustomerCreateView.as_view()),
    path('customers/<int:pk>/', CustomerDetailView.as_view()),
]