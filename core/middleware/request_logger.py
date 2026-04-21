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
