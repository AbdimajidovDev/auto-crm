from modeltranslation.translator import register, TranslationOptions
from .models import Supplier


@register(Supplier)
class SupplierTranslationOptions(TranslationOptions):
    fields = ("name", "description", "address")