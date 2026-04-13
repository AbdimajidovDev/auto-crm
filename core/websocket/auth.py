from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken
from http.cookies import SimpleCookie
from http.cookies import SimpleCookie

User = get_user_model()


@database_sync_to_async
def get_user_from_token(token):
    try:
        valid_data = AccessToken(token)
        return User.objects.get(id=valid_data['user_id'])
    except Exception:
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