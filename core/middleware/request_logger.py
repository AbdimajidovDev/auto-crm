import logging
import time

from django.conf import settings
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger("core.request")


class RequestLoggerMiddleware(MiddlewareMixin):

    METHOD_COLORS = {
        "GET": "🔵",
        "POST": "🟢",
        "PUT": "🟡",
        "PATCH": "🟠",
        "DELETE": "🔴",
    }

    def process_request(self, request):
        request.start_time = time.time()

    def process_response(self, request, response):
        # Productionda har bir so'rovni stdout'ga yozish latency qo'shadi —
        # faqat DEBUG rejimida log yozamiz.
        if not settings.DEBUG:
            return response

        start_time = getattr(request, "start_time", None)
        duration = 0 if start_time is None else time.time() - start_time

        method = request.method.upper()
        icon = self.METHOD_COLORS.get(method, "⚪")
        user = request.user if request.user.is_authenticated else "Anonymous"

        logger.info("%s  [%s %s] %s - %.2fs", icon, user, method, request.path, duration)

        return response


# import time
# from django.utils.deprecation import MiddlewareMixin
#
# class RequestLoggerMiddleware(MiddlewareMixin):
#     def process_request(self, request):
#         request.start_time = time.time()
#
#     def process_response(self, request, response):
#         duration = time.time() - request.start_time
#         print(f"[{request.user} {request.method}] {request.path} - {duration:.2f}s 🚀")
#         return response
#


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Middleware printini logging konfiguratsiyasiga o'tkazish]
# ═══════════════════════════════
