from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken
from http.cookies import SimpleCookie
 # ⚠️ MUAMMO [CLEAN CODE]: `SimpleCookie` importi ikki marta yozilgan.
 # Sabab: dublikat import faylni shovqinli qiladi va lint xatosi beradi.
 # Natija: funksional ta'sir yo'q, lekin code quality pasayadi.
 # ✅ YECHIM:
 # from http.cookies import SimpleCookie
from http.cookies import SimpleCookie  # Eslatma: import dublikat — bittasini qoldirish kifoya.

User = get_user_model()


@database_sync_to_async
def get_user_from_token(token):
    try:
        valid_data = AccessToken(token)
        # ⚠️ MUAMMO [PERFORMANCE]: User to'liq model sifatida olinmoqda.
        # Sabab: websocket scope uchun ko'pincha `id`, `is_active`, role/store kabi cheklangan maydonlar yetarli.
        # Natija: har websocket ulanishida ortiqcha ustunlar o'qiladi.
        # ✅ YECHIM:
        # return User.objects.only("id", "is_active", "is_superuser", "phone_number").get(id=valid_data["user_id"])
        # OPTIMIZATION: kerakli maydonlar bo'lsa `User.objects.only(...).get(...)` yengilroq yuklanadi.
        return User.objects.get(id=valid_data['user_id'])
    except Exception:
        # ⚠️ MUAMMO [CLEAN CODE/XAVFSIZLIK]: Keng `except Exception` token va DB xatolarini bir xil yutadi.
        # Sabab: expired/invalid token bilan DB outage farqlanmaydi.
        # Natija: diagnostika qiyinlashadi va real infra xato yashirinadi.
        # ✅ YECHIM:
        # except (TokenError, User.DoesNotExist):
        #     return AnonymousUser()
        # Eslatma: yuqtirilgan `Exception` juda keng — noto'g'ri token va DB xatoliklari bir xil yo'lga tushadi.
        return AnonymousUser()


class CookieJWTAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        cookie_header = headers.get(b"cookie", b"").decode()

        token = None

        if cookie_header:
            cookie = SimpleCookie()
            cookie.load(cookie_header)

            # ✅ normal cookie
            if "access_token" in cookie:
                token = cookie["access_token"].value

            # 🔥 fallback (sening case)
            else:
                # ⚠️ MUAMMO [XAVFSIZLIK]: Butun cookie header token sifatida qabul qilinmoqda.
                # Sabab: fallback `cookie_header.strip()` boshqa cookie qiymatlarini ham JWT deb yuborishi mumkin.
                # Natija: noto'g'ri auth holati, parsing xatolari va auditda chalkashlik paydo bo'ladi.
                # ✅ YECHIM:
                # Faqat `access_token` cookie yoki `Authorization` headerdagi Bearer tokenni qabul qilish.
                token = cookie_header.strip()

        scope["user"] = await get_user_from_token(token) if token else AnonymousUser()

        return await self.app(scope, receive, send)


#

#
# class CookieJWTAuthMiddleware:
#     def __init__(self, app):
#         self.app = app
#
#     async def __call__(self, scope, receive, send):
#         # 1. Headerlardan cookielarni olish
#         headers = dict(scope.get("headers", []))
#         cookie_header = headers.get(b"cookie", b"").decode()
#
#         token = None
#         if cookie_header:
#             cookie = SimpleCookie()
#             cookie.load(cookie_header)
#             if 'access_token' in cookie:
#                 token = cookie['access_token'].value
#
#         # 2. Foydalanuvchini aniqlash
#         scope["user"] = await get_user_from_token(token) if token else AnonymousUser()
#
#         return await self.app(scope, receive, send)
#
#
#
# # from urllib.parse import parse_qs
# # from channels.middleware import BaseMiddleware
# # from django.contrib.auth.models import AnonymousUser
# # from rest_framework_simplejwt.tokens import UntypedToken
# # from django.contrib.auth import get_user_model
# # from asgiref.sync import sync_to_async
# #
# # User = get_user_model()
# #
# #
# # class TokenAuthMiddleware(BaseMiddleware):
# #     async def __call__(self, scope, receive, send):
# #         query = parse_qs(scope["query_string"].decode())
# #         token = query.get("token")
# #
# #         if token:
# #             try:
# #                 UntypedToken(token[0])
# #                 user = await sync_to_async(User.objects.get)(id=1)  # soddalashtirilgan
# #                 scope["user"] = user
# #             except Exception:
# #                 scope["user"] = AnonymousUser()
# #         else:
# #             scope["user"] = AnonymousUser()
# #
# #         return await super().__call__(scope, receive, send)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Cookie fallback authni qat'iy access_token/Bearer formatiga cheklash]
# ═══════════════════════════════
