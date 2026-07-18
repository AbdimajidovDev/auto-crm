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

    # SLA: har bir API so'rovi 1 sekunddan tez javob berishi kerak.
    # Oshib ketganlari productionda ham WARNING bo'lib yoziladi — sekin
    # endpointlarni kuzatish va optimallashtirish uchun.
    SLOW_REQUEST_THRESHOLD = 1.0

    def process_request(self, request):
        request.start_time = time.time()

    def process_response(self, request, response):
        start_time = getattr(request, "start_time", None)
        duration = 0 if start_time is None else time.time() - start_time

        if duration >= self.SLOW_REQUEST_THRESHOLD:
            logger.warning(
                "🐌 SLOW %.2fs [%s] %s (status=%s)",
                duration, request.method.upper(), request.get_full_path(), response.status_code,
            )

        # Productionda har bir so'rovni stdout'ga yozish latency qo'shadi —
        # faqat DEBUG rejimida log yozamiz.
        if not settings.DEBUG:
            return response

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
