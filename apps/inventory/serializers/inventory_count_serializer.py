from __future__ import annotations

from rest_framework import serializers

from apps.common.quantity import QuantityField
from apps.inventory.models import InventoryCount

# ═══════════════════════════════════════════════
# SERIALIZERS
# ═══════════════════════════════════════════════

class InventoryCountSerializer(serializers.ModelSerializer):
    """
    InventoryCount + annotate qilingan qo'shimcha maydonlar.

    source= ishlatildi — SerializerMethodField shart emas:
      category_name      → product.category.name
      unit_measurement   → product.unit_measurement.measurement

    Annotate dan keladigan maydonlar:
      system_quantity → tizim qoldig'i (ProductBatch.quantity sum)
      diff            → counted_quantity − system_quantity
                        ko'p chiqqan: +N, kam chiqqan: -N
    """
    product_name = serializers.CharField(source="product.name", read_only=True)
    category_name = serializers.CharField(source="product.category.name", read_only=True, default=None)
    unit_measurement = serializers.CharField(source="product.unit_measurement.measurement", read_only=True,
                                             default=None)

    # Annotate dan keladigan maydonlar
    system_quantity = QuantityField(read_only=True)
    diff = QuantityField(read_only=True)
    counted_quantity = QuantityField(read_only=True)

    class Meta:
        model = InventoryCount
        fields = (
            "id",
            "product",
            "product_name",
            "category_name",
            "unit_measurement",
            "counted_quantity",
            "system_quantity",
            "diff",
            "status",
            "is_check",
            "created_at",
        )
