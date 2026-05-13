import time
from django.utils.deprecation import MiddlewareMixin


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
        start_time = getattr(request, "start_time", None)

        if start_time is None:
            duration = 0
        else:
            duration = time.time() - start_time

        method = request.method.upper()
        icon = self.METHOD_COLORS.get(method, "⚪")

        user = request.user if request.user.is_authenticated else "Anonymous"

        # ⚠️ MUAMMO [PERFORMANCE/XAVFSIZLIK]: Middleware har requestda `print` qiladi.
        # Sabab: stdout sync I/O bo'lib, productionda log rotation/level/filter bilan boshqarilmaydi.
        # Natija: yuqori trafikda latency oshadi va user/path ma'lumotlari nazoratsiz loglanadi.
        # ✅ YECHIM:
        # logger.info("request_finished", extra={"method": method, "path": request.path, "duration": duration, "user_id": getattr(request.user, "id", None)})
        # MUAMMO: har so'rovda `print` — production log tizimiga o'tkazish yaxshiroq (yoki faqat DEBUG).
        print(f"{icon}  [{user} {method}] {request.path} - {duration:.2f}s 🚀")

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
