from .base import *

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['*']


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config('DB_NAME'),
        "USER": config('DB_USER'),
        "PASSWORD": config('DB_PASSWORD'),
        "HOST": config('DB_HOST', default='localhost'),
        "PORT": config('DB_PORT', default='5432'),
        "CONN_MAX_AGE": 60,
    }
}

# EXPIRE_TIME = 1
#
FRONTEND_URL = "http://127.0.0.1:8000/api/v1/users/auth"
#
# DOMAIN = "http://127.0.0.1:8000"
#
# BASE_URL = "http://127.0.0.1:8000"

# CHANNEL_LAYERS = {
#     "default": {
#         "BACKEND": "channels.layers.InMemoryChannelLayer",  # dev
#     }
# }