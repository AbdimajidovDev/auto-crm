from .base import *

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS').split(',')


# DATABASE ----------------------------------------->
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='5432'),
    }
}


# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.sqlite3',
#         'NAME': BASE_DIR / 'db.sqlite3',
#     }
# }

# Xavfsizlik --------------------------------------------->
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
