from rest_framework import serializers

from apps.inventory.models import LowStockItem


class LowStockItemSerializer(serializers.ModelSerializer):
    """
    Read-only representation for the low-stock list / history endpoints.

    `store_name` / `product_name` use `source` (not SerializerMethodField) so a
    queryset with `select_related("store", "product")` resolves them with zero
    extra queries.
    """

    store_name = serializers.CharField(source="store.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = LowStockItem
        fields = (
            "id",
            "store",
            "store_name",
            "product",
            "product_name",
            "current_quantity",
            "min_stock",
            "action_type",
            "status",
            "resolved_at",
            "created_at",
        )
        read_only_fields = fields
