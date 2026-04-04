THIRD_PARTY_APPS = [
    # "channels",  # buni ham shu yerda qoldiring
    # "corsheaders",
    "rest_framework",
    "drf_spectacular",
    'rest_framework_simplejwt',
    # 'rest_framework_simplejwt.token_blacklist',
    # 'django_filters',
    # "parler",
    # "parler_rest",
    # 'django_celery_beat',
]

DEFAULT_APPS = [
    'jazzmin',
    # # 'modeltranslation',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

LOCAL_APPS = [
    "apps.users",
    "apps.common",
    "apps.store",
    "apps.contract",
]
