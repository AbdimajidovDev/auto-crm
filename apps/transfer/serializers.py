from apps.products.models import Product


from rest_framework import serializers
from apps.store.models import Store
from apps.transfer.models import StockTransfer, StockTransferItem, Notification


class TransferItemSerializer(serializers.ModelSerializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))
    quantity = serializers.IntegerField(min_value=1)
    # ⚠️ MUAMMO [PERFORMANCE]: `SerializerMethodField` FK maydonga murojaat qiladi.
    # Sabab: `get_product_name()` ichida `obj.product.name` o'qiladi; querysetda `items__product`
    # prefetch qilinmasa har bir item uchun alohida query chiqadi.
    # Natija: transfer listda itemlar soniga proporsional N+1 query yuzaga keladi.
    # ✅ YECHIM:
    # product_name = serializers.CharField(source="product.name", read_only=True)
    product_name = serializers.SerializerMethodField()

    class Meta:
        model = StockTransferItem
        fields = (
            'id', 'product', 'product_name', 'quantity', 'purchase_price', 'selling_price',
        )
        read_only_fields = ('id','product_name', 'purchase_price', 'selling_price',)

    def get_product_name(self, obj):
        return obj.product.name if obj.product else ""

    def validate(self, data):
        # ⚠️ MUAMMO [CLEAN CODE/XAVFSIZLIK]: Serializer validation ichida `print` bor.
        # Sabab: serializer ko'p chaqiriladi va request ma'lumotlarini stdoutga chiqaradi.
        # Natija: log ifloslanadi, maxfiy ma'lumot chiqishi va testlarda shovqin paydo bo'ladi.
        # ✅ YECHIM:
        # logger.debug("Transfer item validated", extra={"product_id": data["product"].id})
        # MUAMMO: productionda `print` — olib tashlash yoki `logging`ga o'tkazish.
        print('data', data)

        # # 🔥 batch borligini tekshiramiz
        # batch = ProductBatch.objects.filter(
        #     store=data["from_store"],
        #     product_id=data["product"]
        # ).first()
        #
        # if not batch:
        #     raise serializers.ValidationError("Product storeda mavjud emas")
        #
        # if batch.quantity < data["quantity"]:
        #     raise serializers.ValidationError({
        #         "detail": "Yetarli stock yo‘q",
        #         "available": batch.quantity
        #     })
        return data


class TransferListSerializer(serializers.ModelSerializer):
    items = TransferItemSerializer(many=True)
    # ⚠️ MUAMMO [PERFORMANCE]: Bir nechta `SerializerMethodField` FK obyektlarini o'qiydi.
    # Sabab: `from_store`, `to_store`, `approved_by` querysetda `select_related` qilinmasa,
    # har transfer uchun 3 tagacha qo'shimcha SQL so'rovi ishlaydi.
    # Natija: list endpoint katta bo'lganda latency keskin oshadi.
    # ✅ YECHIM:
    # from_store_name = serializers.CharField(source="from_store.name", read_only=True)
    # to_store_name = serializers.CharField(source="to_store.name", read_only=True)
    # approved_by_name = serializers.CharField(source="approved_by.full_name", read_only=True)
    from_store_name = serializers.SerializerMethodField()
    to_store_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StockTransfer
        fields = (
            'id', 'from_store', 'from_store_name', 'to_store', 'to_store_name',
            'status', 'created_by', 'approved_by', 'approved_by_name', 'approved_at', 'items'
        )

    def get_from_store_name(self, obj):
        return obj.from_store.name if obj.from_store else ""

    def get_to_store_name(self, obj):
        return obj.to_store.name if obj.to_store else ""

    def get_approved_by_name(self, obj):
        return obj.approved_by.full_name if obj.approved_by else ""


class TransferCreateSerializer(serializers.Serializer):
    # ⚠️ MUAMMO [KRITIK/PERFORMANCE]: `PrimaryKeyRelatedField(queryset=Store.objects.all())` filtrsiz katta jadvalga tayanadi.
    # Sabab: inactive storelar ham validation querysetiga kiradi va indeks/filter siyosati ko'rinmaydi.
    # Natija: noto'g'ri store tanlanishi yoki katta jadvalda ortiqcha lookup xarajati paydo bo'ladi.
    # ✅ YECHIM:
    # from_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.filter(is_active=True))
    # to_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.filter(is_active=True))
    from_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    to_store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    items = TransferItemSerializer(many=True)  # Bir nechta mahsulot

    def validate(self, data):
        if data["from_store"] == data["to_store"]:
            raise serializers.ValidationError("Do'konlar bir xil bo'lmasligi kerak")

        if not data.get("items"):
            raise serializers.ValidationError("Kamida bitta mahsulot bo'lishi shart")

        return data


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            'id', 'user', 'type', 'title', 'message', 'is_read', 'transfer'
        )


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 2
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [SerializerMethodField o'rniga `source` ishlatish va view querysetini select_related/prefetch_related bilan moslash]
# ═══════════════════════════════
