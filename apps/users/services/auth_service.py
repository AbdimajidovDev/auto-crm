from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.template.loader import render_to_string
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings


class PasswordResetService:

    @staticmethod
    def send_reset_email(user):
        token_generator = PasswordResetTokenGenerator()
        token = token_generator.make_token(user)

        uid = urlsafe_base64_encode(force_bytes(user.pk))

        reset_link = f"{settings.FRONTEND_URL}/reset-password/{uid}/{token}/"

        subject = "Password Reset"

        context = {
            "user": user,
            "reset_link": reset_link,
        }

        text_content = f"""
        Click the link below to reset your password:
        {reset_link}

        This link will expire in 1 hour.
        """

        html_content = render_to_string("emails/reset_password.html", context)

        email = EmailMultiAlternatives(
            subject,
            text_content,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
        )

        email.attach_alternative(html_content, "text/html")
        email.send()


#
# import logging
# from django.core.mail import send_mail
# from django.conf import settings
# from django.utils.http import urlsafe_base64_encode
# from django.utils.encoding import force_bytes
# from rest_framework.response import Response
#
# from apps.users.models import UserHistory
# from apps.users.tokens import token_generator
#
# logger = logging.getLogger(__name__)

# class PasswordResetService:
#     @staticmethod
#     def send_reset_email(user):
#         try:
#             uid = urlsafe_base64_encode(force_bytes(user.pk))
#             token = token_generator.make_token(user)
#
#             # Frontend URL (productionda envdan o'qing)
#             reset_url = f"https://smartcitykarshi.uz/reset-password/{uid}/{token}/"  # O'zgartiring
#
#             subject = "Smart City Karshi - Parolni Tiklash"
#             html_message = f"""
#             <html>
#                 <body style="font-family: Arial, sans-serif; color: #333;">
#                     <h2>Assalomu alaykum, {user.get_full_name() or user.username}!</h2>
#                     <p>Parolni tiklash uchun quyidagi tugmani bosing:</p>
#                     <a href="{reset_url}" style="background-color: #4CAF50; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold;">
#                         Parolni Tiklash
#                     </a>
#                     <p>Havola 1 soat ichida amal qiladi.</p>
#                     <p>Agar siz so'rov yubormagan bo'lsangiz, bu xatni e'tiborsiz qoldiring.</p>
#                     <p>Hurmat bilan,<br>Smart City Karshi Jamoasi 🌆</p>
#                 </body>
#             </html>
#             """
#
#             send_mail(
#                 subject=subject,
#                 message="Emailni HTML formatda ko'rsatib bo'lmadi. Brauzerda oching.",
#                 from_email=settings.DEFAULT_FROM_EMAIL,
#                 recipient_list=[user.email],
#                 html_message=html_message,
#                 fail_silently=False,
#             )
#             logger.info(f"Password reset email sent to {user.email}")
#         except Exception as e:
#             logger.error(f"Error sending reset email to {user.email}: {str(e)}")
#             raise  # Viewda handle qilamiz


# ==========================================================================================================
