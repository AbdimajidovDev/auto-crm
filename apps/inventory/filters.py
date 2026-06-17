from __future__ import annotations
from django_filters import rest_framework as filters
from apps.inventory.models import InventoryCount, LowStockItem


# ═══════════════════════════════════════════════
# FILTER
# ═══════════════════════════════════════════════

class InventoryCountFilter(filters.FilterSet):
    """
    ?category=<id>    → kategoriya bo'yicha
    ?is_check=true    → tekshirilgan/tekshirilmagan
    """
    category = filters.NumberFilter(field_name="product__category__id")
    is_check = filters.BooleanFilter(field_name="is_check")

    class Meta:
        model = InventoryCount
        fields = ["category", "is_check"]


class LowStockItemFilter(filters.FilterSet):
    """
    ?action_type=purchase|transfer
    ?store=<id>
    ?product=<id>

    FK filters use NumberFilter (no model resolution at import time, and no
    full-table load when the filter form renders).
    """
    action_type = filters.CharFilter(field_name="action_type", lookup_expr="exact")
    store = filters.NumberFilter(field_name="store__id")
    product = filters.NumberFilter(field_name="product__id")

    class Meta:
        model = LowStockItem
        fields = ["action_type", "store", "product"]

