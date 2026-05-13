from __future__ import annotations
from django_filters import rest_framework as filters
from apps.inventory.models import InventoryCount


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

