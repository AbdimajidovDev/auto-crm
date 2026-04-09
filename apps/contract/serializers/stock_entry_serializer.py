from rest_framework import serializers
from apps.store.models import Store
from apps.contract.models import Supplier, StockEntry
from apps.products.models import Product



class StockEntryItemSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField()
    purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    selling_price = serializers.DecimalField(max_digits=12, decimal_places=2)

    def validate(self, data):
        if data["quantity"] <= 0:
            raise serializers.ValidationError("Quantity > 0 bo‘lishi kerak")

        if data["purchase_price"] <= 0:
            raise serializers.ValidationError("Purchase price noto‘g‘ri")

        if data["selling_price"] <= 0:
            raise serializers.ValidationError("Selling price noto‘g‘ri")

        if data["selling_price"] < data["purchase_price"]:
            raise serializers.ValidationError("Selling price < purchase price bo‘lmasligi kerak")

        return data


class StockEntryCreateSerializer(serializers.Serializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.all())
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    items = StockEntryItemSerializer(many=True)

    def validate(self, data):

        # 🔴 faqat ombor
        if data["store"].type != "b":
            raise serializers.ValidationError("Faqat omborga kirim mumkin")

        if not data["items"]:
            raise serializers.ValidationError("Items bo‘sh bo‘lmasligi kerak")

        return data


class StockEntryListSerializer(serializers.ModelSerializer):
    items = StockEntryItemSerializer(many=True)
    full_name = serializers.SerializerMethodField()
    supplier_name = serializers.SerializerMethodField()
    store_name = serializers.SerializerMethodField()

    class Meta:
        model = StockEntry
        fields = (
            'id', 'supplier', 'supplier_name', 'store', 'store_name', 'created_by', 'full_name', 'items'
        )

    def get_full_name(self, obj):
        return obj.created_by.full_name if hasattr(obj, 'created_by') else "Shaxsiy malumotlar kiritilmagan!"

    def get_supplier_name(self, obj):
        return obj.supplier.name if hasattr(obj, 'supplier') else ""

    def get_store_name(self, obj):
        return obj.store.name if hasattr(obj, 'store') else ""

