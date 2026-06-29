from rest_framework import serializers

from apps.contract.models import Supplier


class StockEntryImportSerializer(serializers.Serializer):
    """
    Excel orqali kirim uchun API darajasidagi maydonlar.
    Mahsulot satrlari (nom/barcode/sku, miqdor, narxlar) Excel faylda keladi.

    Do'kon tanlanmaydi — kirim har doim asosiy do'konga (type='b') qilinadi,
    do'kon view tomonidan avtomatik aniqlanadi.
    """
    supplier = serializers.PrimaryKeyRelatedField(
        queryset=Supplier.objects.filter(is_active=True)
    )
    cash_amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, min_value=0, default=0
    )
    card_amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, min_value=0, default=0
    )
