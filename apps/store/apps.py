from django.apps import AppConfig


class StoreConfig(AppConfig):
    name = 'apps.store'

    def ready(self):
        import apps.store.translation  
