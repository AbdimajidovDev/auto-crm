from django.core.exceptions import ImproperlyConfigured

from .base import *

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = [h.strip() for h in config('ALLOWED_HOSTS').split(',') if h.strip()]

# Prod'da CORS oq ro'yxati bo'sh qolsa frontend umuman ishlamaydi (birorta
# javobda Access-Control-Allow-Origin chiqmaydi) — jim sinishdan ko'ra
# ishga tushishda darhol yiqilgan ma'qul.
if not CORS_ALLOWED_ORIGINS:
    raise ImproperlyConfigured(
        "CORS_ALLOWED_ORIGINS bo'sh. .env ga frontend originlarini yozing, "
        "masalan: CORS_ALLOWED_ORIGINS=https://avtoyon.uz,https://www.avtoyon.uz"
    )


# DATABASE ----------------------------------------->
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='5432'),
        'CONN_MAX_AGE': 60,
        'OPTIONS': {
            'connect_timeout': 10,
        },
    }
}


# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.sqlite3',
#         'NAME': BASE_DIR / 'db.sqlite3',
#     }
# }

# Xavfsizlik --------------------------------------------->
# TLS'ni nginx tugatadi, Django'ga so'rov http bo'lib keladi. Bu headersiz
# SECURE_SSL_REDIRECT cheksiz redirect halqasiga tushadi va request.is_secure()
# doim False bo'ladi. nginx proxy_set_header X-Forwarded-Proto https yuborishi shart.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# CSRF sozlamalari
# CSRF_TRUSTED_ORIGINS = ["https://backend.smart-city-qarshi.uz"]
# SESSION_COOKIE_SAMESITE = 'Lax'
# CSRF_COOKIE_SAMESITE = 'Lax'


# Caching (kelajak uchun tayyor) --------------------------->
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': BASE_DIR / 'cache',
    }
}


# EXPIRE_TIME = 3
#
FRONTEND_URL = config("FRONTEND_URL")
#
# DOMAIN = config("DOMAIN")
#
# BASE_URL = "https://smart-city-qarshi.uz"

# CHANNEL_LAYERS = {
#     "default": {
#         "BACKEND": "channels_redis.core.RedisChannelLayer"
#     }
# }
