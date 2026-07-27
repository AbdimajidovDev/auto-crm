from rest_framework import serializers

from apps.contract.models import StockEntryItem, StockEntryReturn, StockEntryReturnItem


class StockEntryReturnItemInputSerializer(serializers.Serializer):
    """Bitta qaytariladigan satr: kirim satri + miqdor."""
    entry_item = serializers.PrimaryKeyRelatedField(queryset=StockEntryItem.objects.all())
    quantity = serializers.IntegerField(min_value=1)


class StockEntryReturnCreateSerializer(serializers.Serializer):
    """
    POST /contract/entry/<id>/return/ tanasi.
    Satrlar shu kirimga tegishliligi va limitlar servisda (qulf ostida) tekshiriladi.
    """
    items = StockEntryReturnItemInputSerializer(many=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000, default="")

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("Qaytariladigan mahsulotlar tanlanmagan")
        return items


class StockEntryReturnItemListSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True, default="")
    sku = serializers.CharField(source="product.sku", read_only=True, default=None)
    barcode = serializers.CharField(source="product.barcode", read_only=True, default=None)

    class Meta:
        model = StockEntryReturnItem
        fields = (
            "id", "entry_item", "product", "product_name", "sku", "barcode",
            "quantity", "purchase_price", "amount",
        )


class StockEntryReturnListSerializer(serializers.ModelSerializer):
    items = StockEntryReturnItemListSerializer(many=True, read_only=True)
    supplier = serializers.IntegerField(source="entry.supplier_id", read_only=True)
    supplier_name = serializers.CharField(source="entry.supplier.name", read_only=True, default="")
    store = serializers.IntegerField(source="entry.store_id", read_only=True)
    store_name = serializers.CharField(source="entry.store.name", read_only=True, default="")
    full_name = serializers.CharField(source="created_by.full_name", read_only=True, default="")

    class Meta:
        model = StockEntryReturn
        fields = (
            "id", "entry",
            "supplier", "supplier_name",
            "store", "store_name",
            "total_amount", "debt_cancelled", "refund_amount",
            "note", "full_name", "items", "created_at",
        )
