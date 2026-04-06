from django.apps import AppConfig


class ContractConfig(AppConfig):
    name = 'apps.contract'

    def ready(self):
        import apps.contract.translation
