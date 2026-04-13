from rest_framework import serializers
from apps.store.models import Store
from apps.contract.models import Supplier, StockEntry, StockEntryItem
from apps.products.models import Product, ProductBatch


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
    paid_amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0, default=0)
    items = StockEntryItemSerializer(many=True)

    def validate(self, data):
        store = data['store']
        items = data['items']

        if store.type != "b":
            raise serializers.ValidationError("Faqat omborga kirim mumkin")
        if not items:
            raise serializers.ValidationError("Mahsulotlar ro'yxati bo'sh")

        return data



class StockEntryItemListSerializer(serializers.ModelSerializer):
    barcode = serializers.SerializerMethodField()
    shtrix_code = serializers.SerializerMethodField()

    class Meta:
        model = StockEntryItem
        fields = (
            'id', 'product', 'quantity',
            'purchase_price', 'selling_price',
            'barcode', 'shtrix_code'
        )

    def get_barcode(self, obj):
        # Ushbu mahsulot va do'konga tegishli batchni qidiramiz
        batch = ProductBatch.objects.filter(
            product=obj.product,
            store=obj.entry.store
        ).last() # Oxirgi yaratilgan batchni olish uchun
        return batch.barcode if batch else None

    def get_shtrix_code(self, obj):
        batch = ProductBatch.objects.filter(
            product=obj.product,
            store=obj.entry.store
        ).last()
        if batch and batch.shtrix_code:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(batch.shtrix_code.url)
            return batch.shtrix_code.url
        return None


class StockEntryListSerializer(serializers.ModelSerializer):
    items = StockEntryItemListSerializer(many=True)
    full_name = serializers.SerializerMethodField()
    supplier_name = serializers.SerializerMethodField()
    store_name = serializers.SerializerMethodField()
    debt = serializers.SerializerMethodField()

    class Meta:
        model = StockEntry
        fields = (
            'id', 'supplier', 'supplier_name', 'store', 'store_name',
            'paid_amount', 'debt', 'created_by', 'full_name', 'items'
        )

    def get_full_name(self, obj):
        return obj.created_by.full_name if hasattr(obj, 'created_by') else "Shaxsiy malumotlar kiritilmagan!"

    def get_supplier_name(self, obj):
        return obj.supplier.name if hasattr(obj, 'supplier') else ""

    def get_store_name(self, obj):
        return obj.store.name if hasattr(obj, 'store') else ""

    def get_debt(self, obj):
        debt = (obj.total_in or 0) - (obj.total_paid or 0)
        return debt if debt > 0 else 0

    # def get_debt(self, obj):
    #     debt = obj.total_amount - obj.paid_amount
    #     return debt if debt > 0 else 0
