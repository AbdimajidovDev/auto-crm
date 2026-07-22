from decimal import Decimal, InvalidOperation

from rest_framework import serializers

from apps.contract.models import PurchaseSession, Supplier
from apps.sales.models import BankCard
from apps.store.models import Store


class PurchaseSessionItemSerializer(serializers.Serializer):
    """
    Sessiya ichidagi qoralama mahsulot qatori.
    Draft bosqichida qiymatlar to'liq bo'lmasligi mumkin (masalan narx 0),
    shuning uchun bu yerda faqat tip/format tekshiriladi — to'liq biznes
    validatsiya confirm bosqichida (StockEntryCreateSerializer) bajariladi.
    """
    product = serializers.IntegerField(required=False, allow_null=True)
    product_name = serializers.CharField(required=False, allow_blank=True, default="")
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, default=0)
    purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, default=0)
    selling_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, default=0)
    wholesale_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, default=0)


class PurchaseSessionPaymentSerializer(serializers.Serializer):
    """
    Qoralamadagi bitta split to'lov qatori. Draft bosqichida yumshoq tekshiruv
    (amount 0 bo'lishi mumkin) — to'liq biznes validatsiya confirm bosqichida
    StockEntryPaymentInputSerializer orqali bajariladi.
    """
    type = serializers.ChoiceField(choices=(("cash", "cash"), ("card", "card")))
    amount = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=0, default=0)
    bank_card = serializers.IntegerField(required=False, allow_null=True, default=None)


class PurchaseSessionSerializer(serializers.ModelSerializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.filter(is_active=True))
    # Istalgan faol do'kon — kim qaysi do'konga kirim qila olishi view'da
    # tekshiriladi (superuser — hammasiga, xodim — o'z do'koniga)
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.filter(is_active=True))
    bank_card = serializers.PrimaryKeyRelatedField(
        queryset=BankCard.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    items = PurchaseSessionItemSerializer(many=True, required=False)
    payments = PurchaseSessionPaymentSerializer(many=True, required=False)

    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="")
    store_name = serializers.CharField(source="store.name", read_only=True, default="")
    items_count = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseSession
        fields = (
            "id",
            "supplier", "supplier_name",
            "store", "store_name",
            "items", "items_count", "total_amount",
            "cash_amount", "card_amount", "bank_card", "payments",
            "note",
            "status", "current_step",
            "entry",
            "created_at", "updated_at",
        )
        read_only_fields = ("status", "entry", "created_at", "updated_at")

    def get_items_count(self, obj) -> int:
        return len(obj.items or [])

    def get_total_amount(self, obj) -> str:
        total = Decimal("0")
        for item in (obj.items or []):
            try:
                total += Decimal(str(item.get("purchase_price") or 0)) * Decimal(str(item.get("quantity") or 0))
            except (InvalidOperation, TypeError):
                continue
        return f"{total:.2f}"

    def validate_current_step(self, value):
        if value not in (1, 2, 3):
            raise serializers.ValidationError("Bosqich 1, 2 yoki 3 bo'lishi kerak")
        return value

    def validate(self, data):
        bank_card = data.get("bank_card")
        if bank_card is not None and bank_card.scope not in (
            BankCard.Scope.PURCHASE, BankCard.Scope.BOTH
        ):
            raise serializers.ValidationError(
                {"bank_card": "Bu to'lov usuli xarid bo'limi uchun ruxsat etilmagan"}
            )
        return data

    def to_internal_value(self, data):
        validated = super().to_internal_value(data)
        # JSONField'ga Decimal yozib bo'lmaydi — str ko'rinishga o'tkazamiz
        if "items" in validated:
            validated["items"] = [
                {
                    "product": item.get("product"),
                    "product_name": item.get("product_name") or "",
                    "quantity": str(item.get("quantity") or 0),
                    "purchase_price": str(item.get("purchase_price") or 0),
                    "selling_price": str(item.get("selling_price") or 0),
                    "wholesale_price": str(item.get("wholesale_price") or 0),
                }
                for item in validated["items"]
            ]
        if "payments" in validated:
            validated["payments"] = [
                {
                    "type": p.get("type"),
                    "amount": str(p.get("amount") or 0),
                    "bank_card": p.get("bank_card"),
                }
                for p in validated["payments"]
            ]
        return validated
