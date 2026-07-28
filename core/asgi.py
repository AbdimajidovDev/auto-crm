import os
import django # 1. Django ni import qiling

# 2. Sozlamalarni ko'rsating
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

# 3. Django ni rasman ishga tushiring (Barcha applar va modellar yuklanadi)
django.setup()

# 4. Endi boshqa modullarni import qilishingiz mumkin
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

from apps.transfer.routing import websocket_urlpatterns
from core.websocket.auth import CookieJWTAuthMiddleware

# 5. Endi application ni yarating
application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        CookieJWTAuthMiddleware(
            URLRouter(
                websocket_urlpatterns,
            )
        )
    ),
})



# import os
#
# from channels.auth import AuthMiddlewareStack
# from channels.routing import ProtocolTypeRouter, URLRouter
# from django.core.asgi import get_asgi_application
#
# from apps.transfer.routing import websocket_urlpatterns
# from core.websocket.auth import CookieJWTAuthMiddleware
#
# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
#
# django_asgi_app = get_asgi_application()
#
#
#
# application = ProtocolTypeRouter({
#     "http": get_asgi_application(),
#     "websocket": AuthMiddlewareStack( # Standart middlewarelar bilan o'rash
#         CookieJWTAuthMiddleware(
#             URLRouter(
#                 websocket_urlpatterns,
#             )
#         )
#     ),
# })
