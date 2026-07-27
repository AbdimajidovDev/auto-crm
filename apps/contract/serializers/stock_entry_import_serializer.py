from rest_framework import serializers

from apps.contract.models import Supplier
from apps.store.models import Store


class StockEntryImportSerializer(serializers.Serializer):
    """
    Excel orqali kirim uchun API darajasidagi maydonlar.
    Mahsulot satrlari (nom/barcode/sku, miqdor, narxlar) Excel faylda keladi.

    Do'kon tanlansa — kirim shu do'konga qilinadi; tanlanmasa
    (eski mijozlar uchun) asosiy do'kon (type='b') avtomatik aniqlanadi.
    """
    supplier = serializers.PrimaryKeyRelatedField(
        queryset=Supplier.objects.filter(is_active=True)
    )
    store = serializers.PrimaryKeyRelatedField(
        queryset=Store.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )
    cash_amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, min_value=0, default=0
    )
    card_amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, min_value=0, default=0
    )
    # True — bazada topilmagan mahsulotlar Product sifatida yaratilib kirim qilinadi;
    # False — bunday satrlar o'tkazib yuboriladi (skipped ga tushadi)
    create_products = serializers.BooleanField(required=False, default=False)
