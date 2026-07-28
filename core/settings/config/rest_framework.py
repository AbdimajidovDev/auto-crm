REST_FRAMEWORK = {
    # 'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    # 'PAGE_SIZE': 20,  # har sahifada 20 ta (siz xohlaganingizcha o'zgartiring)
    # 'PAGE_SIZE_QUERY_PARAM': 'page_size',  # ?page_size=50 deb o'zgartirish mumkin
    # # 'MAX_PAGE_SIZE': 100,  # maksimal 100 ta
    #
    # "DEFAULT_FILTER_BACKENDS": [
    #     "django_filters.rest_framework.DjangoFilterBackend",
    #     "rest_framework.filters.SearchFilter",
    # ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'apps.common.exception_handler.custom_exception_handler',
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    # BasicAuthentication olib tashlandi: cookie/JWT sxemasida ishlatilmaydi,
    # lekin har bir endpointda throttling'siz parol taxmin qilish yuzasini ochardi.
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.users.authentication.CookieJWTAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    # 'DEFAULT_THROTTLE_CLASSES': [
    #     'rest_framework.throttling.AnonRateThrottle',
    #     'rest_framework.throttling.UserRateThrottle',
    # ],
    # # 'DEFAULT_THROTTLE_RATES': {
    # #     'anon': '3/min',  # Anon users: 3 requests per minute
    # #     'user': '5/min',  # Auth users: 5 requests per minute
    # # },
}


SPECTACULAR_SETTINGS = {
    'TITLE': 'Auto CRM API',
    'DESCRIPTION': 'Auto CRM project',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}
