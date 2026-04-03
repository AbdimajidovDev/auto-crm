from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models import UserHistory
from apps.users.serializers import AdminLoginSerializer
from apps.users.models import User
from apps.users.serializers import (
    ChangePasswordSerializer,
    ForgotPasswordSerializer,
    ResetPasswordSerializer,
)
from apps.users.services import PasswordResetService


@extend_schema(tags=["Login"],
    summary='- Logged out qilish',
)
class AdminLoginAPIView(APIView):
    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    serializer_class = AdminLoginSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)

        if serializer.is_valid():
            user = serializer.validated_data['user']
            tokens = user.get_tokens()

            response = Response({
                "success": True,
                "user_id": user.id,
                "role": user.role,
                "full_name": user.full_name,
                "phone_number": user.phone_number,
            }, status=status.HTTP_200_OK)

            # ACCESS TOKEN COOKIE
            response.set_cookie(
                key="access_token",
                value=tokens["access"],
                httponly=True,
                secure=True,  # Productionda True
                samesite="None",
                max_age=60 * 60,  # 1 soat
            )

            # REFRESH TOKEN COOKIE
            response.set_cookie(
                key="refresh_token",
                value=tokens["refresh"],
                httponly=True,
                secure=True,
                samesite="None",
                max_age=7 * 24 * 60 * 60,  # 7 kun
            )

            # User login qilgan vaqtini saqlab qo'yish ----->
            UserHistory.objects.create(
                user=user,
                action=UserHistory.ActionType.LOGIN,
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT"),
            )
            # ---------------------------------------------->


            return response

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=['Login'],
    summary='- Logged out qilish'
)
class LogOutView(APIView):
    def post(self, request):
        response = Response({"success": True})

        response.delete_cookie("access_token")
        response.delete_cookie("refresh_token")

        # # ======= LogOut vaqtini saqlab qo'yish ========
        UserHistory.objects.create(
            user=request.user,
            action=UserHistory.ActionType.LOGOUT,
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT"),
        )
        # # ==============================================

        return response



@extend_schema(
    tags=["Auth"],
    summary="- Parolni o'zgartirish"
)
class ChangePasswordAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = ChangePasswordSerializer

    def post(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            if not serializer.is_valid():
                return Response({
                    'success': False,
                    'status_code': status.HTTP_400_BAD_REQUEST,
                    'error': serializer.errors
                }, status=status.HTTP_400_BAD_REQUEST)

            old_password = serializer.validated_data.get('old_password')
            new_password = serializer.validated_data.get('new_password')
            user = request.user

            if not user.check_password(old_password):
                return Response({
                    'seccess': False,
                    'status_code': status.HTTP_400_BAD_REQUEST,
                    'message': "Eski parolingiz noto'g'ri!"
                }, status=status.HTTP_400_BAD_REQUEST)

            user.set_password(new_password)
            user.save()
            return Response({
                'success': True,
                'status_code': status.HTTP_200_OK,
                'message': "Parolingiz muvofaqiyatli o'zgartirildi"
            })
        except Exception as e:
            return Response({
                'success': False,
                'status_code': status.HTTP_400_BAD_REQUEST,
                'message': str(e),
            }, status=status.HTTP_400_BAD_REQUEST)



# ===============================   Forgot Password   ====================================

@extend_schema(
    tags=['Auth'],
    summary='- Parolni tiklash uchun emailga link yuborish.'
               )
class ForgotPasswordView(APIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = ForgotPasswordSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]

        user = User.objects.filter(email=email).first()

        if user:
            PasswordResetService.send_reset_email(user)

        # Security: do not reveal whether email exists
        return Response(
            {"detail": f"If the email exists, a reset link has been sent.({user.email})"},
            status=status.HTTP_200_OK
        )


@extend_schema(
    tags=['Auth'],
    summary='- Parolni tiklash.'
)
class ResetPasswordView(APIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = ResetPasswordSerializer

    def post(self, request, uidb64, token):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response(
                {"detail": "Invalid token."},
                status=status.HTTP_400_BAD_REQUEST
            )

        token_generator = PasswordResetTokenGenerator()

        if not token_generator.check_token(user, token):
            return Response(
                {"detail": "Token is invalid or expired."},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(serializer.validated_data["password"])
        user.save()

        return Response(
            {"detail": "Password successfully reset."},
            status=status.HTTP_200_OK
        )
