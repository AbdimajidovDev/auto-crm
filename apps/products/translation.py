from modeltranslation.translator import register, TranslationOptions
from .models import Category, Product, ProductUnitMeasurement, ProductLocation


@register(Category)
class CategoryTranslationOptions(TranslationOptions):
    fields = ("name", "description")

@register(Product)
class ProductTranslationOptions(TranslationOptions):
    fields = ("name", "description")


@register(ProductLocation)
class ProductLocationTranslationOptions(TranslationOptions):
    fields = ("location", "description")


@register(ProductUnitMeasurement)
class ProductUnitMeasurementTranslationOptions(TranslationOptions):
    fields = ("measurement",)
