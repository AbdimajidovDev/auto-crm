from decimal import Decimal

from rest_framework import serializers
from apps.common.quantity import QuantityField, validate_quantity_step
from apps.store.models import Store
from apps.contract.models import Supplier, StockEntry, StockEntryItem, StockEntryPayment
from apps.products.models import Product
from apps.sales.models import BankCard


class StockEntryItemSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(status=Product.ProductStatus.ACTIVE)
    )
    # 0.5 qadam (yarim juft) — faqat is_pair mahsulotlar uchun (validate() da tekshiriladi)
    quantity = QuantityField()
    purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    selling_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    wholesale_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, default=0, min_value=0
    )

    def validate(self, data):
        # >0 va qadam (juft mahsulotda 0.5, oddiyda butun son) birgalikda tekshiriladi
        data["quantity"] = validate_quantity_step(
            data["quantity"],
            is_pair=data["product"].is_pair,
            product_name=data["product"].name,
        )
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


def validate_purchase_bank_card_scope(bank_card):
    """Kirim (xarid) bo'limida faqat scope=purchase/both kartalar ishlatiladi."""
    if bank_card is not None and bank_card.scope not in (
        BankCard.Scope.PURCHASE, BankCard.Scope.BOTH
    ):
        raise serializers.ValidationError(
            {"bank_card": "Bu to'lov usuli xarid bo'limi uchun ruxsat etilmagan"}
        )


class StockEntryPaymentInputSerializer(serializers.Serializer):
    """
    Bitta split to'lov qatori — sotuvdagi PaymentInputSerializer bilan bir xil
    shakl: {type: cash|card, amount, bank_card?}. Karta bo'lsa bank_card majburiy.
    """
    type = serializers.ChoiceField(choices=(("cash", "cash"), ("card", "card")))
    amount = serializers.DecimalField(max_digits=15, decimal_places=2)
    bank_card = serializers.PrimaryKeyRelatedField(
        queryset=BankCard.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )

    def validate(self, data):
        if data["amount"] <= 0:
            raise serializers.ValidationError("To'lov miqdori noldan katta bo'lishi kerak")
        bank_card = data.get("bank_card")
        if data["type"] == "card":
            if bank_card is None:
                raise serializers.ValidationError(
                    {"bank_card": "Karta to'lovi uchun to'lov usulini (kartani) tanlang"}
                )
            validate_purchase_bank_card_scope(bank_card)
        elif bank_card is not None:
            raise serializers.ValidationError(
                {"bank_card": "Naqd to'lovda bank_card yuborilmasligi kerak"}
            )
        return data


class StockEntryCreateSerializer(serializers.Serializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.filter(is_active=True))
    # Istalgan faol do'konga kirim mumkin (ombor ham, savdo do'koni ham).
    # Kim qaysi do'konga kirim qila olishi view'da tekshiriladi
    # (contract.permissions.ensure_store_access): superuser — hammasiga,
    # do'kon xodimi — faqat o'z do'kon(lar)iga.
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.filter(is_active=True))
    # Split to'lovlar (yangi klientlar): har usul alohida qator.
    # Berilsa cash_amount/card_amount/bank_card e'tiborga olinmaydi — ular
    # payments dan hisoblanadi.
    payments = StockEntryPaymentInputSerializer(many=True, required=False)
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

        payments = data.get("payments")
        if payments:
            # Split rejim: summalar payments dan olinadi, yassi maydonlar sinxronlanadi
            data["cash_amount"] = sum(
                (p["amount"] for p in payments if p["type"] == "cash"), Decimal("0")
            )
            data["card_amount"] = sum(
                (p["amount"] for p in payments if p["type"] == "card"), Decimal("0")
            )
            data["bank_card"] = None
            # Bitta karta ikki marta tanlanmasin (taqsimot chalkashmasligi uchun)
            card_ids = [p["bank_card"].id for p in payments if p["type"] == "card"]
            if len(card_ids) != len(set(card_ids)):
                raise serializers.ValidationError(
                    {"payments": "Bitta karta bir necha marta tanlangan"}
                )
            if sum(1 for p in payments if p["type"] == "cash") > 1:
                raise serializers.ValidationError(
                    {"payments": "Naqd to'lov faqat bitta qator bo'lishi mumkin"}
                )

        paid_amount = data["cash_amount"] + data["card_amount"]

        if paid_amount > total_entry_amount:
            raise serializers.ValidationError("To'lov umumiy narxdan oshib ketdi!")

        if not payments:
            bank_card = data.get("bank_card")
            validate_purchase_bank_card_scope(bank_card)
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
    quantity = QuantityField(read_only=True)

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


class StockEntryPaymentListSerializer(serializers.ModelSerializer):
    """Kirimning split to'lov qatorlari (o'qish uchun) — sotuvdagi PaymentSerializer andozasi."""
    bank_card_name = serializers.CharField(source="bank_card.name", read_only=True, default=None)

    class Meta:
        model = StockEntryPayment
        fields = ("id", "amount", "type", "bank_card", "bank_card_name", "created_at")


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
    payments = StockEntryPaymentListSerializer(many=True, read_only=True)

    # source= — select_related orqali SQL yo'q, xotiradan o'qiladi
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="")
    store_name = serializers.CharField(source="store.name", read_only=True, default="")
    full_name = serializers.CharField(source="created_by.full_name", read_only=True,
                                      default="Shaxsiy ma'lumotlar kiritilmagan!")

    # Subquery annotatedan keladigan tayyor qiymatlar
    total_in = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    total_paid = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    # Qaytimlar: total_ret — qarzdan kamaygan qism (ret tranzaksiyalari),
    # returned_amount — qaytarilgan tovar qiymati (refund qismi ham ichida)
    total_ret = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    returned_amount = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
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
            "total_in", "total_paid", "total_ret", "returned_amount", "debt",
            "created_by", "full_name",
            "note",
            "items",
            "payments",
            "created_at",
        )

    def get_debt(self, obj) -> int | float:
        # Qarz = kirim (in) − to'langan (pay) − qaytimlar (ret)
        debt = (obj.total_in or 0) - (obj.total_paid or 0) - (getattr(obj, "total_ret", 0) or 0)
        return debt if debt > 0 else 0


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 2
# Performance muammolari: 2
# Arxitektura muammolari: 1
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [PrimaryKeyRelatedField querysetlarini active/type filterlar bilan cheklash]
# ═══════════════════════════════
