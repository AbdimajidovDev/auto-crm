from rest_framework import serializers

from apps.products.models import Product
from apps.store.models import Store
from apps.writeoff.models import WriteOff, WriteOffItem


# ─────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────

class WriteOffItemCreateSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(status=Product.ProductStatus.ACTIVE)
    )
    quantity = serializers.IntegerField(min_value=1)


class WriteOffCreateSerializer(serializers.Serializer):
    store = serializers.PrimaryKeyRelatedField(queryset=Store.objects.all())
    reason = serializers.ChoiceField(choices=WriteOff.Reason.choices)
    comment = serializers.CharField(required=False, allow_blank=True, default="")
    items = WriteOffItemCreateSerializer(many=True)

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("Hech bo'lmaganda bitta mahsulot kerak.")
        return items


# ─────────────────────────────────────────────
# UPDATE (faqat metama'lumot)
# ─────────────────────────────────────────────

class WriteOffUpdateSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=WriteOff.Reason.choices, required=False)
    comment = serializers.CharField(required=False, allow_blank=True)


# ─────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────

class WriteOffItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_barcode = serializers.CharField(source="product.barcode", read_only=True)

    class Meta:
        model = WriteOffItem
        fields = [
            "id", "product", "product_name", "product_sku", "product_barcode",
            "quantity", "purchase_price", "selling_price",
        ]


# ✅ YAXSHI: WriteOffItemSerializer product ma'lumotlarini source= orqali oladi
#   (SerializerMethodField EMAS) — Detail viewdagi prefetch_related("items__product") bilan
#   N+1 bo'lmaydi. items ni loop ichida qayta so'rov yo'q.
class WriteOffListSerializer(serializers.ModelSerializer):
    # ✅ YAXSHI: store_name/created_by_name source= bilan — view select_related bergani uchun
    #   qo'shimcha query yo'q. items_count annotate'dan IntegerField sifatida o'qiladi,
    #   har qator uchun alohida COUNT so'rovi (N+1) bo'lmaydi.
    store_name = serializers.CharField(source="store.name", read_only=True)
    reason_display = serializers.CharField(source="get_reason_display", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default=None)
    items_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = WriteOff
        fields = [
            "id", "store", "store_name", "reason", "reason_display",
            "total_amount", "items_count", "created_by", "created_by_name",
            "inventory_session", "created_at",
        ]


class WriteOffDetailSerializer(serializers.ModelSerializer):
    store_name = serializers.CharField(source="store.name", read_only=True)
    reason_display = serializers.CharField(source="get_reason_display", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default=None)
    items = WriteOffItemSerializer(many=True, read_only=True)

    class Meta:
        model = WriteOff
        fields = [
            "id", "store", "store_name", "reason", "reason_display",
            "comment", "total_amount", "created_by", "created_by_name",
            "inventory_session", "created_at", "items",
        ]


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 10 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [—]
# Izoh: Barcha related maydonlar source= orqali (SerializerMethodField/loop ichida query yo'q).
#       items_count annotate'dan o'qiladi. N+1 faqat view select_related/prefetch bermasa
#       yuzaga keladi — write_off_view'da to'g'ri berilgan.
# ═══════════════════════════════