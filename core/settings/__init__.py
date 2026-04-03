from decouple import config

ENVIRONMENT = config('ENVIRONMENT', default='dev').lower()

if ENVIRONMENT == 'prod':
    from .prod import *
else:
    from .dev import *
