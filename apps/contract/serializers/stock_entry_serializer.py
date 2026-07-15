from rest_framework import serializers
from apps.store.models import Store
from apps.contract.models import Supplier, StockEntry, StockEntryItem
from apps.products.models import Product
from apps.sales.models import BankCard


class StockEntryItemSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(status=Product.ProductStatus.ACTIVE)
    )
    quantity = serializers.IntegerField()
    purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    selling_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    wholesale_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, default=0, min_value=0
    )

    def validate(self, data):
        if data["quantity"] <= 0:
            raise serializers.ValidationError("Quantity > 0 bo'lishi kerak")
        if data["purchase_price"] <= 0:
            raise serializers.ValidationError("Purchase price noto'g'ri")
        if data["selling_price"] <= 0:
            raise serializers.ValidationError("Selling price noto'g'ri")
        if data["selling_price"] < data["purchase_price"]:
            raise serializers.ValidationError("Selling price < purchase price bo'lmasligi kerak")

        wholesale_price = data["wholesale_price"]
        if wholesale_price > 0:
            if wholesale_price < data["purchase_price"]:
                raise serializers.ValidationError(
                    "Optom narx tannarxdan (purchase price) past bo'lmasligi kerak"
                )
            if wholesale_price > data["selling_price"]:
                raise serializers.ValidationError(
                    "Optom narx oddiy sotish narxidan (selling price) yuqori bo'lmasligi kerak"
                )
        return data


class StockEntryCreateSerializer(serializers.Serializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.filter(is_active=True))
    # Istalgan faol do'konga kirim mumkin (ombor ham, savdo do'koni ham).
    # Kim qaysi do'konga kirim qila olishi view'da tekshiriladi
    # (contract.permissions.ensure_store_access): superuser — hammasiga,
    # do'kon xodimi — faqat o'z do'kon(lar)iga.
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.filter(is_active=True))
    cash_amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0, default=0)
    card_amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0, default=0)
    # Karta to'lovida qaysi usul/karta ishlatilgani. Ixtiyoriy (eski klientlar
    # va Excel import uchun), lekin berilsa scope kirimga mos bo'lishi shart.
    bank_card = serializers.PrimaryKeyRelatedField(
        queryset=BankCard.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )
    # Ixtiyoriy izoh/tavsif
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000, default="")

    items = StockEntryItemSerializer(many=True)

    def validate(self, data):
        items = data['items']

        if not items:
            raise serializers.ValidationError("Mahsulotlar ro'yxati bo'sh")

        total_entry_amount = sum(
            item["purchase_price"] * item["quantity"] for item in items
        )
        paid_amount = data["cash_amount"] + data["card_amount"]

        if paid_amount > total_entry_amount:
            raise serializers.ValidationError("To'lov umumiy narxdan oshib ketdi!")

        bank_card = data.get("bank_card")
        if bank_card is not None:
            if bank_card.scope not in (BankCard.Scope.PURCHASE, BankCard.Scope.BOTH):
                raise serializers.ValidationError(
                    {"bank_card": "Bu to'lov usuli xarid bo'limi uchun ruxsat etilmagan"}
                )
        if data["card_amount"] > 0 and bank_card is None:
            raise serializers.ValidationError(
                {"bank_card": "Karta to'lovi uchun to'lov usulini tanlang"}
            )

        return data


class StockEntryItemListSerializer(serializers.ModelSerializer):
    barcode = serializers.SerializerMethodField()
    shtrix_code = serializers.SerializerMethodField()
    sku = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()

    class Meta:
        model = StockEntryItem
        fields = (
            "id", "product", "product_name", "quantity",
            "purchase_price", "selling_price",
             "sku", "barcode", "shtrix_code",
        )

    def get_product_name(self, obj):
        return obj.product.name if obj.product else None

    def get_sku(self, obj):
        return obj.product.sku if obj.product else None

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
            "total_amount", "paid_amount",
            "total_in", "total_paid", "debt",
            "created_by", "full_name",
            "note",
            "items",
            "created_at",
        )

    def get_debt(self, obj) -> int | float:
        debt = (obj.total_in or 0) - (obj.total_paid or 0)
        return debt if debt > 0 else 0


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 2
# Performance muammolari: 2
# Arxitektura muammolari: 1
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [PrimaryKeyRelatedField querysetlarini active/type filterlar bilan cheklash]
# ═══════════════════════════════
