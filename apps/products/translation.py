from modeltranslation.translator import register, TranslationOptions
from .models import Category, Product


@register(Category)
class CategoryTranslationOptions(TranslationOptions):
    fields = ("name", "description")

@register(Product)
class ProductTranslationOptions(TranslationOptions):
    fields = ("name", "description")