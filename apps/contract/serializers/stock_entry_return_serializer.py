from rest_framework import serializers

from apps.common.quantity import QuantityField, validate_quantity_step
from apps.contract.models import StockEntryItem, StockEntryReturn, StockEntryReturnItem


class StockEntryReturnItemInputSerializer(serializers.Serializer):
    """Bitta qaytariladigan satr: kirim satri + miqdor."""
    entry_item = serializers.PrimaryKeyRelatedField(
        queryset=StockEntryItem.objects.select_related("product")
    )
    quantity = QuantityField()

    def validate(self, data):
        # Juft mahsulotda 0.5 qadam, oddiyda faqat butun son
        product = data["entry_item"].product
        data["quantity"] = validate_quantity_step(
            data["quantity"], is_pair=product.is_pair, product_name=product.name
        )
        return data


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
    quantity = QuantityField(read_only=True)

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
