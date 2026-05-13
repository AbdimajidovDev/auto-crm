from rest_framework import serializers

from apps.inventory.models import InventoryMovement, InventorySession


class InventoryListSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventorySession
        fields = (
            'id', 'store', 'started_by', 'started_at', 'status', 'snapshot_taken'
        )


class InventoryDetailSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(source="product.id")
    product_name = serializers.CharField(source="product.name")
    barcode = serializers.CharField()

    declared = serializers.IntegerField(source="expected_quantity")
    scanned = serializers.IntegerField(source="counted")

    sold_out = serializers.IntegerField()
    returned = serializers.IntegerField()
    transfer_out = serializers.IntegerField()
    transfer_in = serializers.IntegerField()
    entry = serializers.IntegerField()

    status = serializers.CharField()
    is_check = serializers.BooleanField()

    # ⚠️ MUAMMO [PERFORMANCE/CLEAN CODE]: `final` va `difference` serializer ichida qayta hisoblanadi.
    # Sabab: `difference` `get_final()` ni yana chaqiradi, hisob-kitob queryset annotate yoki service qatlamida emas.
    # Natija: katta listda Python darajasida takroriy hisoblash va serializer mas'uliyati ortadi.
    # ✅ YECHIM:
    # final = serializers.IntegerField(read_only=True)
    # difference = serializers.IntegerField(read_only=True)
    # Querysetda `final` va `difference` ni annotate qilish.
    final = serializers.SerializerMethodField()
    difference = serializers.SerializerMethodField()

    def get_final(self, obj):
        return (
                obj.counted
                - obj.sold_out
                - obj.transfer_out
                + obj.transfer_in
                + obj.entry
                + obj.returned
        )

    def get_difference(self, obj):
        # OPTIMIZATION: `get_final` ikki marta chaqiriladi (get_final + bu yerda) — kichik overhead,
        # lekin juda katta ro'yxatlarda bitta o'zgaruvchida saqlab qayta ishlatish mumkin.
        final = self.get_final(obj)
        return final - obj.expected_quantity


class InventoryStartSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()


class InventoryCountSerializer(serializers.Serializer):
    session_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=0)


class InventoryFinalizeSerializer(serializers.Serializer):
    session_id = serializers.IntegerField()


class InventoryCancelSerializer(serializers.Serializer):
    session_id = serializers.IntegerField()


class InventoryMovementListSerializer(serializers.ModelSerializer):
    # ⚠️ MUAMMO [PERFORMANCE]: `SerializerMethodField` product FK ga murojaat qiladi.
    # Sabab: view querysetida `select_related("product")` bo'lmasa har movement uchun query chiqadi.
    # Natija: movement listda N+1 query muammosi yuzaga keladi.
    # ✅ YECHIM:
    # product_name = serializers.CharField(source="product.name", read_only=True)
    product_name = serializers.SerializerMethodField()

    class Meta:
        model = InventoryMovement
        fields = (
            'id', 'session', 'product', 'product_name', 'quantity', 'type', 'ref_id', 'created_at',
        )

    def get_product_name(self, obj):
        # N+1: view querysetida `select_related("product")` bo'lmasa har bir qator uchun qo'shimcha so'rov.
        return obj.product.name if obj.product else ''


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 2
# Arxitektura muammolari: 1
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [InventoryMovementListSerializer product_name ni source fieldga o'tkazish va final/difference ni annotate qilish]
# ═══════════════════════════════
