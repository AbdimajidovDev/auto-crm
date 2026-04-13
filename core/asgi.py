import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

from apps.transfer.routing import websocket_urlpatterns
from core.websocket.auth import CookieJWTAuthMiddleware

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_app = get_asgi_application()



application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack( # Standart middlewarelar bilan o'rash
        CookieJWTAuthMiddleware(
            URLRouter(
                websocket_urlpatterns,
            )
        )
    ),
})

# application = ProtocolTypeRouter({
#     "http": django_asgi_app,
#     "websocket": TokenAuthMiddleware(
#         URLRouter(
#             websocket_urlpatterns
#         )
#     ),
# })
