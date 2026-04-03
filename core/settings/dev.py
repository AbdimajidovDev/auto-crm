from .base import *

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['*']


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
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