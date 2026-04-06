from modeltranslation.translator import register, TranslationOptions
from .models import Store


@register(Store)
class StoreTranslationOptions(TranslationOptions):
    fields = ("name", "address")