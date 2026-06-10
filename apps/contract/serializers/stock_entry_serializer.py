from rest_framework import serializers
from apps.store.models import Store
from apps.contract.models import Supplier, StockEntry, StockEntryItem
from apps.products.models import Product, ProductBatch


class StockEntryItemSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(status=Product.ProductStatus.ACTIVE)
    )
    # product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
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
    # ⚠️ MUAMMO [KRITIK/PERFORMANCE]: Supplier va Store querysetlari `.all()` bilan cheklanmagan.
    # Sabab: inactive/deleted supplier/storelar validationga tushishi mumkin, store type filter ham field darajasida emas.
    # Natija: noto'g'ri kirim obyektlari yaratilishi va katta jadvalda sekin lookup yuzaga keladi.
    # ✅ YECHIM:
    # supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.filter(is_active=True))
    # store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.filter(is_active=True, type="b"))
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.all())
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    paid_amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0, default=0)
    payment_type = serializers.ChoiceField(choices=StockEntry.PaymentType.choices)
    items = StockEntryItemSerializer(many=True)

    def validate(self, data):
        store = data['store']
        items = data['items']

        if store.type != "b":
            raise serializers.ValidationError("Faqat omborga kirim mumkin")
        if not items:
            raise serializers.ValidationError("Mahsulotlar ro'yxati bo'sh")

        return data


# ─────────────────────────────────────────────
# SERIALIZERS
# ─────────────────────────────────────────────

class StockEntryItemListSerializer(serializers.ModelSerializer):
    barcode = serializers.SerializerMethodField()
    shtrix_code = serializers.SerializerMethodField()

    class Meta:
        model = StockEntryItem
        fields = (
            "id", "product", "quantity",
            "purchase_price", "selling_price",
            "barcode", "shtrix_code",
        )

    def get_barcode(self, obj):
        return obj.product.barcode if obj.product else None

    def get_shtrix_code(self, obj):
        if not obj.product or not obj.product.shtrix_code:
            return None
        request = self.context.get("request")
        return (
            request.build_absolute_uri(obj.product.shtrix_code.url)
            if request else obj.product.shtrix_code.url
        )


    # def _get_batch(self, obj):
    #     """
    #     Prefetch qilingan fetched_batches dan entry.store ga mos batchni qaytaradi.
    #     SQL yo'q — xotiradan qidiriladi.
    #
    #     Agar prefetch yo'q bo'lsa (masalan detail viewda) — fallback sifatida
    #     DB ga murojaat qiladi, lekin bu holat ro'yxat viewda bo'lmaydi.
    #     """
    #     # ⚠️ MUAMMO [CLEAN CODE]: `store_id = obj.entry_id` noto'g'ri nomlangan va ishlatilmaydi.
    #     # Sabab: entry_id store_id emas; o'zgaruvchi keyingi kodda foydalanilmagan.
    #     # Natija: kelajakdagi maintainer noto'g'ri assumption bilan bug kiritishi mumkin.
    #     # ✅ YECHIM:
    #     # Bu satrni olib tashlash yoki kerak bo'lsa `entry_id = obj.entry_id` deb nomlash.
    #     store_id = obj.entry_id  # entry.store_id ni olish uchun quyida ko'rsatilgan
    #
    #     # Prefetch to_attr orqali kelgan list
    #     fetched = getattr(obj.product, "fetched_batches", None)
    #     if fetched is not None:
    #         # entry.store_id — entry select_related orqali yuklanadi
    #         entry_store_id = obj.entry.store_id
    #         matched = [b for b in fetched if b.store_id == entry_store_id]
    #         return matched[-1] if matched else None
    #
    #     # Fallback: prefetch bo'lmasa DB dan oladi (detail view uchun)
    #     return (
    #         ProductBatch.objects
    #         .filter(product=obj.product, store=obj.entry.store, status=Product.ProductStatus.ACTIVE)
    #         .last()
    #     )


    # def get_barcode(self, obj):
    #     batch = self._get_batch(obj)
    #     return batch.barcode if batch else None

    # def get_shtrix_code(self, obj):
    #     batch = self._get_batch(obj)
    #     if not batch or not batch.shtrix_code:
    #         return None
    #     request = self.context.get("request")
    #     return (
    #         request.build_absolute_uri(batch.shtrix_code.url)
    #         if request else batch.shtrix_code.url
    #     )


class StockEntryListSerializer(serializers.ModelSerializer):
    """
    SerializerMethodField → source= ga o'tkazildi:
      get_supplier_name → source="supplier.name"
      get_store_name    → source="store.name"
      get_full_name     → source="created_by.full_name"

    hasattr() muammosi:
      Avvalgi kod hasattr(obj, 'created_by') ishlatgan edi.
      Bu NOTO'G'RI — created_by har doim mavjud (RelatedManager bor),
      faqat qiymati None bo'lishi mumkin (null=True).
      hasattr True qaytaradi, keyin obj.created_by.full_name → AttributeError!

      Yechim: source= + default=None (None bo'lsa None qaytaradi, xato yo'q).

    debt:
      Subquery annotatedan kelgan to'g'ri total_in va total_paid
      asosida hisoblanadi — kartezian muammo yo'q.
    """
    items = StockEntryItemListSerializer(many=True, read_only=True)

    # source= — select_related orqali SQL yo'q, xotiradan o'qiladi
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="")
    store_name = serializers.CharField(source="store.name", read_only=True, default="")
    full_name = serializers.CharField(source="created_by.full_name", read_only=True,
                                      default="Shaxsiy ma'lumotlar kiritilmagan!")

    # Subquery annotatedan keladigan tayyor qiymatlar
    total_in = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    total_paid = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    # ⚠️ MUAMMO [ARXITEKTURA]: `debt` hisob-kitobi serializer methodda qolgan.
    # Sabab: `total_in` va `total_paid` annotate qilingan, yakuniy debt ham querysetda annotate qilinsa filter/order mumkin bo'ladi.
    # Natija: biznes formula serializerga bog'lanadi.
    # ✅ YECHIM:
    # debt = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    # Querysetda `debt=Greatest(F("total_in") - F("total_paid"), Value(0))` annotate qilish.
    debt = serializers.SerializerMethodField()

    class Meta:
        model = StockEntry
        fields = (
            "id",
            "supplier", "supplier_name",
            "store", "store_name",
            "paid_amount",
            "total_in", "total_paid", "debt",
            "created_by", "full_name",
            "items",
            "created_at",
        )

    def get_debt(self, obj) -> int | float:
        debt = (obj.total_in or 0) - (obj.total_paid or 0)
        return debt if debt > 0 else 0


# class StockEntryItemListSerializer(serializers.ModelSerializer):
#     barcode = serializers.SerializerMethodField()
#     shtrix_code = serializers.SerializerMethodField()
#
#     class Meta:
#         model = StockEntryItem
#         fields = (
#             'id', 'product', 'quantity',
#             'purchase_price', 'selling_price',
#             'barcode', 'shtrix_code'
#         )
#
#     def get_barcode(self, obj):
#         # N+1: har bir kirim qatori uchun `ProductBatch.objects.filter(...).last()` — ro'yxat uzunligi
#         # bilan proporsional qo'shimcha so'rovlar. `get_shtrix_code` ham xuddi shu filterni takrorlaydi
#         # (har bir item uchun 2x). Batchni prefetch yoki annotate bilan yig'ish yaxshiroq.
#         # Ushbu mahsulot va do'konga tegishli batchni qidiramiz
#         batch = ProductBatch.objects.filter(
#             product=obj.product,
#             store=obj.entry.store
#         ).last() # Oxirgi yaratilgan batchni olish uchun
#         return batch.barcode if batch else None
#
#     def get_shtrix_code(self, obj):
#         batch = ProductBatch.objects.filter(
#             product=obj.product,
#             store=obj.entry.store
#         ).last()
#         if batch and batch.shtrix_code:
#             request = self.context.get('request')
#             if request:
#                 return request.build_absolute_uri(batch.shtrix_code.url)
#             return batch.shtrix_code.url
#         return None
#
#
# class StockEntryListSerializer(serializers.ModelSerializer):
#     items = StockEntryItemListSerializer(many=True)
#     full_name = serializers.SerializerMethodField()
#     supplier_name = serializers.SerializerMethodField()
#     store_name = serializers.SerializerMethodField()
#     debt = serializers.SerializerMethodField()
#
#     class Meta:
#         model = StockEntry
#         fields = (
#             'id', 'supplier', 'supplier_name', 'store', 'store_name',
#             'paid_amount', 'debt', 'created_by', 'full_name', 'items'
#         )
#
#     def get_full_name(self, obj):
#         return obj.created_by.full_name if hasattr(obj, 'created_by') else "Shaxsiy malumotlar kiritilmagan!"
#
#     def get_supplier_name(self, obj):
#         return obj.supplier.name if hasattr(obj, 'supplier') else ""
#
#     def get_store_name(self, obj):
#         return obj.store.name if hasattr(obj, 'store') else ""
#
# #     def get_debt(self, obj):
# #       debt = (obj.total_in or 0) - (obj.total_paid or 0)
# #       return debt if debt > 0 else 0
#
#     # def get_debt(self, obj):
#     #     debt = obj.total_amount - obj.paid_amount
#     #     return debt if debt > 0 else 0


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 2
# Performance muammolari: 2
# Arxitektura muammolari: 1
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [PrimaryKeyRelatedField querysetlarini active/type filterlar bilan cheklash]
# ═══════════════════════════════
